"""
Evaluation script for Import/Export RAG Chatbot.
Sends 10 test queries, records answers, sources, and response times.
Outputs results as JSON for analysis.
"""
import requests
import time
import json

BASE_URL = "http://127.0.0.1:8080/get"

# 10-query evaluation set
TEST_QUERIES = [
    {
        "id": 1,
        "category": "Export Data (CSV)",
        "query": "What does India export to the USA?",
        "expected_source": "Export Data",
        "expected_info": "Commodity names, HS codes, USD values",
    },
    {
        "id": 2,
        "category": "Legal / Regulatory (Laws KB)",
        "query": "What documents are needed for exporting goods from India?",
        "expected_source": "Trade Laws & Regulations KB",
        "expected_info": "IEC, shipping bill, invoice, packing list",
    },
    {
        "id": 3,
        "category": "Book Knowledge (PDF)",
        "query": "How do I start an import export business?",
        "expected_source": "Book",
        "expected_info": "Steps to start, licensing, market research",
    },
    {
        "id": 4,
        "category": "Export Data (CSV)",
        "query": "Show me India's export data to UAE with commodities and values",
        "expected_source": "Export Data",
        "expected_info": "Commodities, HS codes, growth % for UAE",
    },
    {
        "id": 5,
        "category": "Legal / Regulatory (Laws KB)",
        "query": "What are the customs regulations for importing into the EU?",
        "expected_source": "Trade Laws & Regulations KB",
        "expected_info": "CE marking, REACH, EU customs rules",
    },
    {
        "id": 6,
        "category": "Book Knowledge (PDF)",
        "query": "What is a letter of credit in international trade?",
        "expected_source": "Book",
        "expected_info": "LC definition, parties involved, process",
    },
    {
        "id": 7,
        "category": "Cross-Source (Multi)",
        "query": "I want to export textiles from India. What regulations should I follow and what is the current trade volume?",
        "expected_source": "Multiple sources",
        "expected_info": "Export data + legal requirements combined",
    },
    {
        "id": 8,
        "category": "Export Data (CSV)",
        "query": "Which commodities does India export to China?",
        "expected_source": "Export Data",
        "expected_info": "Commodity names and HS codes for China",
    },
    {
        "id": 9,
        "category": "Book Knowledge (PDF)",
        "query": "Explain the role of freight forwarders in international trade",
        "expected_source": "Book",
        "expected_info": "Freight forwarder responsibilities",
    },
    {
        "id": 10,
        "category": "Out-of-Scope (Hallucination Test)",
        "query": "What is the capital of France?",
        "expected_source": "None",
        "expected_info": "Should refuse or admit out of context",
    },
]

def run_evaluation():
    results = []
    total_time = 0

    for test in TEST_QUERIES:
        print(f"\n{'='*60}")
        print(f"Query {test['id']}/{len(TEST_QUERIES)}: [{test['category']}]")
        print(f"Q: {test['query']}")
        print(f"{'='*60}")

        start = time.time()
        try:
            resp = requests.post(
                BASE_URL,
                data={"msg": test["query"]},
                timeout=180,  # 3 min timeout for slow LLM
            )
            elapsed = round(time.time() - start, 2)
            total_time += elapsed

            if resp.status_code == 200:
                data = resp.json()
                answer = data.get("answer", "")
                sources = data.get("sources", [])

                print(f"✅ Response ({elapsed}s)")
                print(f"Sources: {sources}")
                print(f"Answer (first 300 chars): {answer[:300]}...")

                results.append({
                    "id": test["id"],
                    "category": test["category"],
                    "query": test["query"],
                    "answer": answer,
                    "sources": sources,
                    "response_time_sec": elapsed,
                    "status": "success",
                    "expected_source": test["expected_source"],
                    "expected_info": test["expected_info"],
                })
            else:
                elapsed = round(time.time() - start, 2)
                total_time += elapsed
                error_msg = resp.text[:200]
                print(f"❌ HTTP {resp.status_code} ({elapsed}s): {error_msg}")
                results.append({
                    "id": test["id"],
                    "category": test["category"],
                    "query": test["query"],
                    "answer": f"HTTP Error {resp.status_code}",
                    "sources": [],
                    "response_time_sec": elapsed,
                    "status": "error",
                    "expected_source": test["expected_source"],
                    "expected_info": test["expected_info"],
                })

        except requests.exceptions.Timeout:
            elapsed = round(time.time() - start, 2)
            total_time += elapsed
            print(f"⏰ Timeout ({elapsed}s)")
            results.append({
                "id": test["id"],
                "category": test["category"],
                "query": test["query"],
                "answer": "TIMEOUT",
                "sources": [],
                "response_time_sec": elapsed,
                "status": "timeout",
                "expected_source": test["expected_source"],
                "expected_info": test["expected_info"],
            })

        except Exception as e:
            elapsed = round(time.time() - start, 2)
            total_time += elapsed
            print(f"💥 Exception ({elapsed}s): {e}")
            results.append({
                "id": test["id"],
                "category": test["category"],
                "query": test["query"],
                "answer": str(e),
                "sources": [],
                "response_time_sec": elapsed,
                "status": "exception",
                "expected_source": test["expected_source"],
                "expected_info": test["expected_info"],
            })

    # Save results
    output_path = "/Users/udaydhakar12/Documents/project_impexp/scratch/evaluation_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    # Print summary
    successes = sum(1 for r in results if r["status"] == "success")
    avg_time = total_time / len(results) if results else 0

    print(f"\n{'='*60}")
    print(f"EVALUATION COMPLETE")
    print(f"{'='*60}")
    print(f"Total queries: {len(results)}")
    print(f"Successful: {successes}/{len(results)}")
    print(f"Total time: {round(total_time, 2)}s")
    print(f"Avg response time: {round(avg_time, 2)}s")
    print(f"Results saved to: {output_path}")

if __name__ == "__main__":
    run_evaluation()
