#!/usr/bin/env python3
import statistics
import sys
import time

sys.path.insert(0, ".")

from md_sync.translate.fallback import _detect_provider, translate_via_api


def test_provider(provider_name: str, test_cases: list[tuple[str, str]]) -> dict:
    """Test translation for a given provider with test cases."""
    results = {
        "provider": provider_name,
        "success": 0,
        "failed": 0,
        "times": [],
        "translations": [],
    }

    for zh_text, expected_en in test_cases:
        start = time.perf_counter()
        result = translate_via_api(
            zh_text, provider=provider_name, source_lang="zh", target_lang="en"
        )
        elapsed = time.perf_counter() - start
        results["times"].append(elapsed)

        if result:
            results["success"] += 1
            results["translations"].append((zh_text, result))
            print(f"[✓] {provider_name}: '{zh_text[:20]}...' → '{result}' ({elapsed * 1000:.1f}ms)")
        else:
            results["failed"] += 1
            print(f"[✗] {provider_name}: '{zh_text[:20]}...' → FAILED ({elapsed * 1000:.1f}ms)")

    return results


def main():
    test_cases = [
        ("你好，这是一个测试。", "Hello, this is a test."),
        ("请翻译这些中文文字。", "Please translate these Chinese characters."),
        ("机器翻译需要准确。", "Machine translation requires accuracy."),
        ("快速高效的实现方式。", "A fast and efficient implementation."),
        ("API服务正在测试中。", "API service is being tested."),
    ]

    print("=" * 60)
    print("翻译API测试 - 2026-08-01")
    print("=" * 60)
    print(f"检测默认provider: {_detect_provider()}")
    print()

    providers = ["google", "bing"]
    all_results = []

    for provider in providers:
        print(f"\n--- 测试 {provider} 翻译接口 ---")
        try:
            result = test_provider(provider, test_cases)
            all_results.append(result)
        except Exception as e:
            print(f"[E] {provider}测试异常: {e}")
            all_results.append({"provider": provider, "error": str(e)})

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    for r in all_results:
        if "error" in r:
            print(f"{r['provider']}: 错误 - {r['error']}")
        else:
            avg_time = statistics.mean(r["times"]) * 1000 if r["times"] else 0
            print(
                f"{r['provider']}: 成功 {r['success']}/{r['success'] + r['failed']}, 平均响应 {avg_time:.1f}ms"
            )

    # 返回总体成功状态
    total_success = sum(r.get("success", 0) for r in all_results)
    total_tests = sum(len(test_cases) for _ in providers)
    print(f"\n总体通过率: {total_success}/{total_tests}")

    return total_success == total_tests


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
