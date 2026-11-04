# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Changed
- Toolchains: Go 1.27, Rust 1.99 / edition 2024, JDK 25 (LTS), Node.js 26 (LTS), .NET 10 SDK pinned via `global.json`; Python CI now tests 3.11 (floor) and 3.15. CI actions updated to their latest majors.
- Libraries: TypeScript 5.6 -> 7.0, Vitest 2 -> 5; xUnit v2 -> xUnit v3 (on Microsoft.Testing.Platform, opted in via `global.json`), Microsoft.NET.Test.Sdk 18; JUnit 5 -> 6, Jackson 2 -> 3 (`tools.jackson`; malformed price books still raise `UncheckedIOException`), Surefire 3.6, Compiler plugin 3.16.

## [0.1.0] - 2026-08-09

First public release. single-pass cost & latency attribution by feature/tenant/model with budget, anomaly, and creep gates, in Python, C#, and Java.

[0.1.0]: https://github.com/parag-labs/token-lens/releases/tag/v0.1.0
