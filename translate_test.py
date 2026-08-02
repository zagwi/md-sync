#!/usr/bin/env python3
"""Simple test of translation functionality using existing md-sync modules."""
import sys
import time

sys.path.insert(0, '.')

# Test direct translation via the working fallback module
from md_sync.translate.fallback import translate_via_api

def test_translation():
    print("Testing translation with MyMemory (no API key)...")
    
    # Test sentences from README (Chinese to English)
    test_cases = [
        "你用中文或英文写一份 Markdown 源文件",
        "md-sync 会自动把它翻译成另一种语言",
        "一份源 -> 多份产物",
        "源文件改了 -> 自动重新同步",
        "翻译走 缓存优先"
    ]
    
    success = 0
    total = len(test_cases)
    
    for i, zh_text in enumerate(test_cases):
        print(f"\nTest {i+1}/{total}: {zh_text}")
        start = time.perf_counter()
        # Try MyMemory first (no key required)
        result = translate_via_api(zh_text, provider='google', source_lang='zh', target_lang='en')
        elapsed = time.perf_counter() - start
        
        if result:
            success += 1
            print(f"  -> {result} ({elapsed*1000:.0f}ms)")
        else:
            print(f"  -> FAILED ({elapsed*1000:.0f}ms)")
            # Try Bing as backup
            result = translate_via_api(zh_text, provider='bing', source_lang='zh', target_lang='en')
            if result:
                success += 1
                print(f"  -> (Bing) {result}")
    
    print(f"\nResult: {success}/{total} tests")
    return success == total

if __name__ == "__main__":
    success = test_translation()
    sys.exit(0 if success else 1)
