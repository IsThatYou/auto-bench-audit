"""Moved to bench_audit.config. This module keeps the older import path working."""

from ..config import BenchmarkConfig, EvidenceCollectionConfig, load_benchmark_config, load_collection_config

__all__ = ["BenchmarkConfig", "EvidenceCollectionConfig", "load_benchmark_config", "load_collection_config"]
