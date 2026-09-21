package tokenlens

import "testing"

func rec(model, feature string, in, out int64, latency float64) UsageRecord {
	return UsageRecord{
		Model: model, InputTokens: in, OutputTokens: out,
		LatencyMs: latency, Feature: feature, Tenant: "acme",
	}
}

func TestAggregateGroupsByFeature(t *testing.T) {
	records := []UsageRecord{
		rec("gpt-4o", "search", 1000, 500, 100),
		rec("gpt-4o", "search", 2000, 1000, 200),
		rec("gpt-4o-mini", "chat", 1000, 1000, 50),
	}
	stats, err := Aggregate(records, "feature")
	if err != nil {
		t.Fatal(err)
	}
	if len(stats) != 2 {
		t.Fatalf("expected 2 dimensions, got %d", len(stats))
	}
	search := stats["search"]
	if search.Calls != 2 {
		t.Fatalf("search calls = %d", search.Calls)
	}
	if search.InputTokens != 3000 || search.OutputTokens != 1500 {
		t.Fatalf("search tokens = %d/%d", search.InputTokens, search.OutputTokens)
	}
	if search.TotalTokens() != 4500 {
		t.Fatalf("search total tokens = %d", search.TotalTokens())
	}
	approx(t, search.AvgLatencyMs(), 150.0)
}

func TestAggregateByTenantAndModel(t *testing.T) {
	records := []UsageRecord{
		{Model: "gpt-4o", InputTokens: 100, OutputTokens: 100, Feature: "a", Tenant: "t1"},
		{Model: "gpt-4o-mini", InputTokens: 100, OutputTokens: 100, Feature: "b", Tenant: "t2"},
	}
	byTenant, err := Aggregate(records, "tenant")
	if err != nil {
		t.Fatal(err)
	}
	if len(byTenant) != 2 || byTenant["t1"].Calls != 1 {
		t.Fatal("tenant aggregation is wrong")
	}
	byModel, err := Aggregate(records, "model")
	if err != nil {
		t.Fatal(err)
	}
	if byModel["gpt-4o"].Calls != 1 {
		t.Fatal("model aggregation is wrong")
	}
}

func TestAggregateUnknownDimension(t *testing.T) {
	if _, err := Aggregate([]UsageRecord{rec("gpt-4o", "a", 1, 1, 1)}, "nope"); err == nil {
		t.Fatal("expected an error for an unknown dimension")
	}
}

func TestAggregateEmpty(t *testing.T) {
	stats, err := Aggregate(nil, "feature")
	if err != nil {
		t.Fatal(err)
	}
	if len(stats) != 0 {
		t.Fatal("expected no stats for no records")
	}
}

func TestAvgLatencyZeroCalls(t *testing.T) {
	s := &DimensionStat{Key: "x"}
	approx(t, s.AvgLatencyMs(), 0)
}

func TestDetectAnomaliesNeedsThreeDimensions(t *testing.T) {
	stats := map[string]*DimensionStat{
		"a": {Key: "a", Cost: 1},
		"b": {Key: "b", Cost: 100},
	}
	if got := DetectAnomalies(stats, 3.0); len(got) != 0 {
		t.Fatalf("expected no anomalies with 2 dimensions, got %d", len(got))
	}
}

func TestDetectAnomaliesFlagsOutlier(t *testing.T) {
	stats := map[string]*DimensionStat{
		"a": {Key: "a", Cost: 1},
		"b": {Key: "b", Cost: 1},
		"c": {Key: "c", Cost: 50},
	}
	got := DetectAnomalies(stats, 3.0)
	if len(got) != 1 {
		t.Fatalf("expected 1 anomaly, got %d", len(got))
	}
	if got[0].Key != "c" || got[0].Metric != "cost" {
		t.Fatalf("wrong anomaly: %+v", got[0])
	}
	approx(t, got[0].Baseline, 1)
	approx(t, got[0].Factor, 50)
}

func TestDetectAnomaliesZeroMedian(t *testing.T) {
	stats := map[string]*DimensionStat{
		"a": {Key: "a", Cost: 0},
		"b": {Key: "b", Cost: 0},
		"c": {Key: "c", Cost: 5},
	}
	if got := DetectAnomalies(stats, 3.0); len(got) != 0 {
		t.Fatal("a zero median must not produce anomalies")
	}
}

func TestDetectAnomaliesSortedByFactorDesc(t *testing.T) {
	// median of [1,1,1,10,40] is 1, so both 10 and 40 clear the 3x threshold
	stats := map[string]*DimensionStat{
		"a": {Key: "a", Cost: 1},
		"b": {Key: "b", Cost: 1},
		"c": {Key: "c", Cost: 1},
		"d": {Key: "d", Cost: 10},
		"e": {Key: "e", Cost: 40},
	}
	got := DetectAnomalies(stats, 3.0)
	if len(got) != 2 {
		t.Fatalf("expected 2 anomalies, got %d", len(got))
	}
	if got[0].Key != "e" || got[1].Key != "d" {
		t.Fatalf("anomalies not sorted by factor desc: %+v", got)
	}
	approx(t, got[0].Factor, 40)
	approx(t, got[1].Factor, 10)
}

func TestDetectAnomaliesMedianIsUpperMiddle(t *testing.T) {
	// with an even count the median is the upper-middle element (index len/2),
	// matching the Python reference: median of [1,1,10,40] is 10, so only 40 clears 3x
	stats := map[string]*DimensionStat{
		"a": {Key: "a", Cost: 1},
		"b": {Key: "b", Cost: 1},
		"c": {Key: "c", Cost: 10},
		"d": {Key: "d", Cost: 40},
	}
	got := DetectAnomalies(stats, 3.0)
	if len(got) != 1 || got[0].Key != "d" {
		t.Fatalf("expected only d to be flagged, got %+v", got)
	}
	approx(t, got[0].Baseline, 10)
	approx(t, got[0].Factor, 4)
}

func TestBuildReportTotalsAndBudget(t *testing.T) {
	records := []UsageRecord{
		rec("gpt-4o", "search", 1_000_000, 1_000_000, 10),
		rec("gpt-4o", "chat", 1_000_000, 1_000_000, 20),
	}
	budget := 20.0
	report, err := BuildReport(records, "feature", &budget, 3.0)
	if err != nil {
		t.Fatal(err)
	}
	approx(t, report.TotalCost, 25.0) // 12.5 + 12.5
	if report.TotalCalls != 2 {
		t.Fatalf("total calls = %d", report.TotalCalls)
	}
	if !report.BudgetExceeded {
		t.Fatal("25.0 should exceed a budget of 20.0")
	}
}

func TestBuildReportNoBudget(t *testing.T) {
	records := []UsageRecord{rec("gpt-4o", "search", 1_000_000, 1_000_000, 10)}
	report, err := BuildReport(records, "feature", nil, 3.0)
	if err != nil {
		t.Fatal(err)
	}
	if report.BudgetExceeded {
		t.Fatal("no budget configured must never report a breach")
	}
}

func TestBuildReportBudgetExactlyMetIsNotExceeded(t *testing.T) {
	records := []UsageRecord{rec("gpt-4o", "search", 1_000_000, 1_000_000, 10)}
	budget := 12.5
	report, _ := BuildReport(records, "feature", &budget, 3.0)
	if report.BudgetExceeded {
		t.Fatal("cost equal to budget is not a breach")
	}
}

func timedRec(feature string, in, out int64, ts float64) UsageRecord {
	return UsageRecord{
		Model: "gpt-4o", InputTokens: in, OutputTokens: out,
		Feature: feature, Tenant: "acme", Timestamp: ts,
	}
}

func TestDetectCreepFlagsRisingRate(t *testing.T) {
	records := []UsageRecord{
		// baseline window (t < 100): one cheap call
		timedRec("search", 1000, 1000, 10),
		// recent window: several expensive calls
		timedRec("search", 1_000_000, 1_000_000, 150),
		timedRec("search", 1_000_000, 1_000_000, 180),
		timedRec("search", 1_000_000, 1_000_000, 199),
	}
	split := 100.0
	got, err := DetectCreep(records, "feature", &split, 2.0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Key != "search" {
		t.Fatalf("expected search to be flagged, got %+v", got)
	}
	if got[0].RecentRate <= got[0].BaselineRate {
		t.Fatal("recent rate must exceed baseline")
	}
}

func TestDetectCreepIgnoresNewDimensions(t *testing.T) {
	// "chat" only appears in the recent window -> new, not creep
	records := []UsageRecord{
		timedRec("search", 1000, 1000, 10),
		timedRec("chat", 1_000_000, 1_000_000, 150),
	}
	split := 100.0
	got, _ := DetectCreep(records, "feature", &split, 2.0)
	for _, c := range got {
		if c.Key == "chat" {
			t.Fatal("a brand-new dimension is not creep")
		}
	}
}

func TestDetectCreepNeedsTwoTimedRecords(t *testing.T) {
	got, _ := DetectCreep([]UsageRecord{timedRec("a", 1, 1, 10)}, "feature", nil, 2.0)
	if len(got) != 0 {
		t.Fatal("one timed record cannot show a trend")
	}
}

func TestDetectCreepIgnoresUntimedRecords(t *testing.T) {
	got, _ := DetectCreep([]UsageRecord{
		rec("gpt-4o", "a", 1, 1, 1), // Timestamp 0 -> untimed
		rec("gpt-4o", "a", 1, 1, 1),
	}, "feature", nil, 2.0)
	if len(got) != 0 {
		t.Fatal("untimed records must be ignored")
	}
}

func TestDetectCreepIdenticalTimestamps(t *testing.T) {
	got, _ := DetectCreep([]UsageRecord{
		timedRec("a", 1, 1, 50),
		timedRec("a", 1, 1, 50),
	}, "feature", nil, 2.0)
	if len(got) != 0 {
		t.Fatal("a zero-width window cannot show a trend")
	}
}

func TestDetectCreepDefaultSplitIsMidpoint(t *testing.T) {
	// timestamps must be > 0 to count as timed; midpoint of [10,100] is 55
	records := []UsageRecord{
		timedRec("search", 1000, 1000, 10),
		timedRec("search", 1_000_000, 1_000_000, 100),
	}
	got, err := DetectCreep(records, "feature", nil, 2.0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("expected the midpoint split to flag creep, got %+v", got)
	}
	approx(t, got[0].Factor, 1000)
}

func TestDetectCreepZeroTimestampIsUntimed(t *testing.T) {
	// a timestamp of 0 means "not timed", so only one record is usable here
	records := []UsageRecord{
		timedRec("search", 1000, 1000, 0),
		timedRec("search", 1_000_000, 1_000_000, 100),
	}
	got, _ := DetectCreep(records, "feature", nil, 2.0)
	if len(got) != 0 {
		t.Fatalf("expected no creep with a single timed record, got %+v", got)
	}
}

func TestDetectCreepUnknownDimension(t *testing.T) {
	records := []UsageRecord{timedRec("a", 1, 1, 10), timedRec("a", 1, 1, 20)}
	if _, err := DetectCreep(records, "nope", nil, 2.0); err == nil {
		t.Fatal("expected an error for an unknown dimension")
	}
}

func TestUsageRecordHelpers(t *testing.T) {
	r := rec("gpt-4o", "search", 1_000_000, 1_000_000, 10)
	approx(t, r.Cost(), 12.5)
	if r.TotalTokens() != 2_000_000 {
		t.Fatalf("total tokens = %d", r.TotalTokens())
	}
}
