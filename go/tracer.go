package tokenlens

import (
	"fmt"
	"math"
	"sort"
)

// UsageRecord is one LLM call: what it cost, how long it took, and who it was for.
type UsageRecord struct {
	Model        string
	InputTokens  int64
	OutputTokens int64
	LatencyMs    float64
	Feature      string
	Tenant       string
	Timestamp    float64 // epoch seconds; used by the rolling-window creep detector
}

// Cost rates the record against the default price book.
func (r UsageRecord) Cost() float64 { return CostOf(r.Model, r.InputTokens, r.OutputTokens) }

// TotalTokens is input plus output.
func (r UsageRecord) TotalTokens() int64 { return r.InputTokens + r.OutputTokens }

// DimensionStat accumulates usage for one value of a dimension.
type DimensionStat struct {
	Key          string
	Calls        int
	InputTokens  int64
	OutputTokens int64
	Cost         float64
	LatencySum   float64
}

// AvgLatencyMs is the mean latency across the dimension's calls.
func (s *DimensionStat) AvgLatencyMs() float64 {
	if s.Calls == 0 {
		return 0
	}
	return math.Round(s.LatencySum/float64(s.Calls)*100) / 100
}

// TotalTokens is input plus output.
func (s *DimensionStat) TotalTokens() int64 { return s.InputTokens + s.OutputTokens }

// Anomaly flags a dimension that is expensive relative to its peers right now.
type Anomaly struct {
	Key      string
	Metric   string
	Value    float64
	Baseline float64
	Factor   float64
}

// Creep flags a dimension whose cost rate is rising over time.
//
// The median detector only catches a dimension that is expensive relative to its
// peers right now. It misses one that is slowly growing but still cheaper than
// the noisy ones. Comparing a recent window against an earlier baseline window
// catches that trend.
type Creep struct {
	Key          string
	BaselineRate float64 // cost per second over the baseline window
	RecentRate   float64 // cost per second over the recent window
	Factor       float64
}

// Report is the aggregate view: spend by dimension, budget state, and anomalies.
type Report struct {
	TotalCost      float64
	TotalCalls     int
	ByDimension    map[string]*DimensionStat
	BudgetExceeded bool
	Anomalies      []Anomaly
}

func dimensionOf(r UsageRecord, dimension string) (string, error) {
	switch dimension {
	case "feature":
		return r.Feature, nil
	case "tenant":
		return r.Tenant, nil
	case "model":
		return r.Model, nil
	default:
		return "", fmt.Errorf("unknown dimension %q", dimension)
	}
}

// Aggregate rolls records up by the given dimension.
func Aggregate(records []UsageRecord, dimension string) (map[string]*DimensionStat, error) {
	stats := make(map[string]*DimensionStat)
	for _, r := range records {
		key, err := dimensionOf(r, dimension)
		if err != nil {
			return nil, err
		}
		s, ok := stats[key]
		if !ok {
			s = &DimensionStat{Key: key}
			stats[key] = s
		}
		s.Calls++
		s.InputTokens += r.InputTokens
		s.OutputTokens += r.OutputTokens
		s.Cost = Round6(s.Cost + r.Cost())
		s.LatencySum += r.LatencyMs
	}
	return stats, nil
}

// DetectAnomalies flags dimensions whose cost exceeds factor x the median dimension cost.
func DetectAnomalies(stats map[string]*DimensionStat, factor float64) []Anomaly {
	costs := make([]float64, 0, len(stats))
	for _, s := range stats {
		costs = append(costs, s.Cost)
	}
	if len(costs) < 3 {
		return nil
	}
	sort.Float64s(costs)
	median := costs[len(costs)/2]
	if median <= 0 {
		return nil
	}
	out := make([]Anomaly, 0)
	for _, s := range stats {
		if s.Cost > factor*median {
			out = append(out, Anomaly{
				Key:      s.Key,
				Metric:   "cost",
				Value:    s.Cost,
				Baseline: median,
				Factor:   math.Round(s.Cost/median*100) / 100,
			})
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Factor > out[j].Factor })
	return out
}

// DetectCreep flags dimensions whose cost rate rose from a baseline window to a recent one.
//
// Records with Timestamp < splitTime form the baseline window; the rest form the
// recent window. Cost is normalized by each window's duration to get a rate
// (cost/second), so uneven window lengths compare fairly. A dimension is flagged
// when its recent rate exceeds factor times its baseline rate.
//
// When splitTime is nil, the midpoint of the observed timestamp range is used.
func DetectCreep(records []UsageRecord, dimension string, splitTime *float64, factor float64) ([]Creep, error) {
	timed := make([]UsageRecord, 0, len(records))
	for _, r := range records {
		if r.Timestamp > 0 {
			timed = append(timed, r)
		}
	}
	if len(timed) < 2 {
		return nil, nil
	}

	lo, hi := timed[0].Timestamp, timed[0].Timestamp
	for _, r := range timed {
		lo = math.Min(lo, r.Timestamp)
		hi = math.Max(hi, r.Timestamp)
	}
	if hi == lo {
		return nil, nil
	}

	split := (lo + hi) / 2
	if splitTime != nil {
		split = *splitTime
	}
	baseDur := math.Max(split-lo, 1e-9)
	recentDur := math.Max(hi-split, 1e-9)

	baseCost := make(map[string]float64)
	recentCost := make(map[string]float64)
	for _, r := range timed {
		key, err := dimensionOf(r, dimension)
		if err != nil {
			return nil, err
		}
		if r.Timestamp < split {
			baseCost[key] += r.Cost()
		} else {
			recentCost[key] += r.Cost()
		}
	}

	out := make([]Creep, 0)
	for key, rc := range recentCost {
		baseRate := baseCost[key] / baseDur
		recentRate := rc / recentDur
		if baseRate <= 0 {
			continue // no baseline to compare against; new, not creep
		}
		if recentRate > factor*baseRate {
			out = append(out, Creep{
				Key:          key,
				BaselineRate: math.Round(baseRate*1e9) / 1e9,
				RecentRate:   math.Round(recentRate*1e9) / 1e9,
				Factor:       math.Round(recentRate/baseRate*100) / 100,
			})
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Factor > out[j].Factor })
	return out, nil
}

// BuildReport aggregates records and evaluates budget + anomalies in one pass.
// A nil budget means "no budget configured".
func BuildReport(records []UsageRecord, dimension string, budget *float64, anomalyFactor float64) (*Report, error) {
	stats, err := Aggregate(records, dimension)
	if err != nil {
		return nil, err
	}
	total := 0.0
	calls := 0
	for _, s := range stats {
		total += s.Cost
		calls += s.Calls
	}
	total = Round6(total)
	return &Report{
		TotalCost:      total,
		TotalCalls:     calls,
		ByDimension:    stats,
		BudgetExceeded: budget != nil && total > *budget,
		Anomalies:      DetectAnomalies(stats, anomalyFactor),
	}, nil
}
