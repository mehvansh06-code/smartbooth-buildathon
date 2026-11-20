"""
verify_pg_pruning_and_search.py
Automated benchmark and verification script for PostgreSQL 17 partition pruning and two-stage Indic search.

1. EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) validation of strict partition pruning.
2. Comprehensive latency benchmark across 100+ queries (Hindi, English, House No, EPIC).
3. Stage 1 (PostgreSQL) vs Stage 2 (rapidfuzz) breakdown with p50, p90, p95, p99 metrics.
"""

import sys
import os
import time
import json
import statistics
import psycopg2

# Configure UTF-8 stdout
sys.stdout.reconfigure(encoding='utf-8')

# Add PLATFORM directory to sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from electionapp.search.two_stage_search import search_voters, explain_search, execute_stage1, stage2_rapidfuzz_rank

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "postgres",
    "dbname": "voter_platform"
}

def extract_scanned_relations(plan_node: dict) -> list:
    """Recursively extract all relation names accessed in an EXPLAIN plan."""
    relations = []
    if "Relation Name" in plan_node:
        relations.append(plan_node["Relation Name"])
    for child in plan_node.get("Plans", []):
        relations.extend(extract_scanned_relations(child))
    return list(set(relations))

def verify_partition_pruning(conn):
    """
    Verifies that querying a specific tenant_id strictly prunes non-target partitions.
    """
    print("\n=======================================================")
    print("STEP 1: VERIFYING POSTGRESQL 17 PARTITION PRUNING")
    print("=======================================================")

    test_tenants = [
        {"tenant_id": 1, "expected_partition": "voters_t1", "disallowed": ["voters_t2", "voters_t3"]},
        {"tenant_id": 2, "expected_partition": "voters_t2", "disallowed": ["voters_t1", "voters_t3"]},
        {"tenant_id": 3, "expected_partition": "voters_t3", "disallowed": ["voters_t1", "voters_t2"]},
    ]

    pruning_verified = True

    for item in test_tenants:
        tid = item["tenant_id"]
        exp = item["expected_partition"]
        disallowed = item["disallowed"]

        plan = explain_search(conn, tid, "राम")
        top_plan = plan["Plan"]
        scanned_relations = extract_scanned_relations(top_plan)

        print(f"\n[Tenant ID {tid}]")
        print(f"  Target Partition Expected : {exp}")
        print(f"  Actual Partitions Scanned : {scanned_relations}")
        print(f"  Execution Time            : {plan.get('Execution Time', 0):.3f} ms")
        print(f"  Planning Time             : {plan.get('Planning Time', 0):.3f} ms")

        # Verify target partition is scanned
        if exp not in scanned_relations:
            print(f"  ❌ FAILED: Expected partition {exp} was NOT scanned!")
            pruning_verified = False
        else:
            print(f"  ✅ SUCCESS: Expected partition {exp} scanned.")

        # Verify no disallowed partitions are touched
        leaked = [d for d in disallowed if d in scanned_relations]
        if leaked:
            print(f"  ❌ FAILED: Cross-partition leak detected! Touched: {leaked}")
            pruning_verified = False
        else:
            print(f"  ✅ SUCCESS: Zero cross-partition leakage! {disallowed} completely pruned.")

    if not pruning_verified:
        raise AssertionError("Partition pruning verification failed!")
    print("\n>>> ALL PARTITION PRUNING CHECKS PASSED PERFECTLY <<<\n")

def run_latency_benchmark(conn, num_queries: int = 120):
    """
    Executes a comprehensive battery of 100+ queries and measures Stage 1, Stage 2, and Total latencies.
    """
    print("=======================================================")
    print("STEP 2: TWO-STAGE INDIC SEARCH LATENCY BENCHMARK")
    print("=======================================================")

    # Corpus of 120 diverse queries covering all domains
    hindi_queries = [
        "राम", "राजेश", "सुरेश", "शान्ति", "दिनेश", "कमला", "सुनीता", "प्रकाश",
        "मोहन", "मुकेश", "महेन्द्र", "कैलाश", "गोपाल", "संतोष", "लक्ष्मण", "पवन",
        "अनिल", "विनोद", "पूजा", "राधा", "गीता", "माया", "प्रेमचंद", "प्रसन्न",
        "जबरा", "ओम", "श्याम", "भंवर", "किशन", "तारा", "आशा", "उर्मिला", "मंजू",
        "देवी", "कुमार", "लाल", "सिंह", "बाई", "सोनी", "जैन"
    ]

    english_queries = [
        "ram", "rajesh", "suresh", "shanti", "dinesh", "kamla", "sunita", "prakash",
        "mohan", "mukesh", "mahendra", "kailash", "gopal", "santosh", "laxman", "pawan",
        "anil", "vinod", "pooja", "radha", "geeta", "maya", "premchand", "prasann",
        "jabra", "om", "shyam", "bhanwar", "kishan", "tara", "devi", "kumar"
    ]

    compound_queries = [
        "राम लाल", "राजेश कुमार", "कमला देवी", "शान्ति लाल", "मोहन लाल", "सुरेश कुमार",
        "रमेश चन्द", "माया सेन", "प्रसन्न मेहता", "जबरा राम", "कैलाश चन्द", "दिनेश कुमार",
        "राम किशन", "ओम प्रकाश", "सुनीता देवी", "ram lal", "rajesh kumar", "kamla devi",
        "mohan lal", "suresh kumar", "om prakash"
    ]

    house_no_queries = [
        "0", "1", "2", "3", "4", "5", "10", "12", "15", "20", "24", "42",
        "50", "77", "100", "101", "104", "150", "201", "279", "300", "421"
    ]

    epic_queries = [
        "RLJ1213180", "WAN1196518", "YJW1837764", "RLJ", "WAN", "YJW", "1837764",
        "1213180", "1196518", "RLJ12", "WAN11", "YJW18"
    ]

    test_corpus = []
    # Build balanced query list
    for q in hindi_queries:
        test_corpus.append({"category": "Hindi Name", "query": q, "tenant_id": 3})
    for q in english_queries:
        test_corpus.append({"category": "English Name", "query": q, "tenant_id": 3})
    for q in compound_queries:
        test_corpus.append({"category": "Compound Name", "query": q, "tenant_id": 3})
    for q in house_no_queries:
        test_corpus.append({"category": "House No", "query": q, "tenant_id": 3})
    for q in epic_queries:
        test_corpus.append({"category": "EPIC Token", "query": q, "tenant_id": 3})

    # Also test Tenant 1 (Jelatra) and Tenant 2 (Ward 9)
    test_corpus.append({"category": "Tenant 1 Indic", "query": "जबरा राम", "tenant_id": 1})
    test_corpus.append({"category": "Tenant 1 English", "query": "Jabra Ram", "tenant_id": 1})
    test_corpus.append({"category": "Tenant 1 EPIC", "query": "RLJ1213180", "tenant_id": 1})
    test_corpus.append({"category": "Tenant 2 Indic", "query": "माया", "tenant_id": 2})
    test_corpus.append({"category": "Tenant 2 English", "query": "hashinta", "tenant_id": 2})
    test_corpus.append({"category": "Tenant 2 EPIC", "query": "WAN1196518", "tenant_id": 2})

    print(f"Total Benchmark Queries: {len(test_corpus)} queries across 3 tenants")

    # Warmup queries
    for _ in range(5):
        search_voters(conn, 3, "राम", limit=10)

    # Metrics storage
    stage1_times_ms = []
    stage2_times_ms = []
    total_times_ms = []
    sample_outputs = []

    for i, item in enumerate(test_corpus):
        tid = item["tenant_id"]
        q = item["query"]
        cat = item["category"]

        # Stage 1 timing
        t0 = time.perf_counter()
        candidates = execute_stage1(conn, tid, q, candidate_limit=200)
        t1 = time.perf_counter()

        # Stage 2 timing
        results = stage2_rapidfuzz_rank(candidates, q, limit=50)
        t2 = time.perf_counter()

        s1_ms = (t1 - t0) * 1000.0
        s2_ms = (t2 - t1) * 1000.0
        tot_ms = (t2 - t0) * 1000.0

        stage1_times_ms.append(s1_ms)
        stage2_times_ms.append(s2_ms)
        total_times_ms.append(tot_ms)

        if i < 8 or i % 25 == 0:
            top_hit = results[0]["name_hi"] or results[0]["name_en"] if results else "No Match"
            score = results[0]["search_score"] if results else 0
            sample_outputs.append({
                "i": i + 1,
                "cat": cat,
                "query": q,
                "tenant_id": tid,
                "hits": len(results),
                "top": top_hit,
                "score": score,
                "s1_ms": round(s1_ms, 2),
                "s2_ms": round(s2_ms, 2),
                "tot_ms": round(tot_ms, 2)
            })

    # Output Sample Runs
    print("\n--- SAMPLE QUERY BENCHMARK RUNS ---")
    print(f"{'#':<4} {'Tenant':<8} {'Category':<15} {'Query':<15} {'Hits':<6} {'Top Hit':<18} {'S1(ms)':<8} {'S2(ms)':<8} {'Total(ms)':<8}")
    print("-" * 95)
    for s in sample_outputs:
        print(f"{s['i']:<4} T{s['tenant_id']:<7} {s['cat']:<15} {s['query']:<15} {s['hits']:<6} {s['top']:<18} {s['s1_ms']:<8.2f} {s['s2_ms']:<8.2f} {s['tot_ms']:<8.2f}")

    # Statistical Aggregations
    def percentile(data, pct):
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * (pct / 100.0))
        return sorted_data[min(idx, len(sorted_data) - 1)]

    p50_tot = percentile(total_times_ms, 50)
    p90_tot = percentile(total_times_ms, 90)
    p95_tot = percentile(total_times_ms, 95)
    p99_tot = percentile(total_times_ms, 99)
    mean_tot = statistics.mean(total_times_ms)

    p50_s1 = percentile(stage1_times_ms, 50)
    p95_s1 = percentile(stage1_times_ms, 95)
    p50_s2 = percentile(stage2_times_ms, 50)
    p95_s2 = percentile(stage2_times_ms, 95)

    print("\n=======================================================")
    print("BENCHMARK SUMMARY RESULTS (100+ QUERIES)")
    print("=======================================================")
    print(f"Total Queries Measured : {len(test_corpus)}")
    print(f"Total Latency (Mean)   : {mean_tot:.2f} ms")
    print(f"Total Latency (p50)    : {p50_tot:.2f} ms")
    print(f"Total Latency (p90)    : {p90_tot:.2f} ms")
    print(f"Total Latency (p95)    : {p95_tot:.2f} ms  (Target: < 15.00 ms)")
    print(f"Total Latency (p99)    : {p99_tot:.2f} ms")
    print(f"Min / Max Latency      : {min(total_times_ms):.2f} ms / {max(total_times_ms):.2f} ms")
    print("-" * 55)
    print(f"Stage 1 (PostgreSQL DB): p50 = {p50_s1:.2f} ms | p95 = {p95_s1:.2f} ms")
    print(f"Stage 2 (SIMD rapidfuzz): p50 = {p50_s2:.2f} ms | p95 = {p95_s2:.2f} ms")
    print("=======================================================\n")

    benchmark_report = {
        "num_queries": len(test_corpus),
        "mean_latency_ms": round(mean_tot, 2),
        "p50_ms": round(p50_tot, 2),
        "p90_ms": round(p90_tot, 2),
        "p95_ms": round(p95_tot, 2),
        "p99_ms": round(p99_tot, 2),
        "min_ms": round(min(total_times_ms), 2),
        "max_ms": round(max(total_times_ms), 2),
        "stage1_p50_ms": round(p50_s1, 2),
        "stage1_p95_ms": round(p95_s1, 2),
        "stage2_p50_ms": round(p50_s2, 2),
        "stage2_p95_ms": round(p95_s2, 2)
    }

    report_path = os.path.join(BASE_DIR, "tests", "benchmark_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(benchmark_report, f, indent=2)
    print(f"Saved benchmark report to {report_path}")

    return benchmark_report

if __name__ == "__main__":
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        verify_partition_pruning(conn)
        report = run_latency_benchmark(conn)
    finally:
        conn.close()
