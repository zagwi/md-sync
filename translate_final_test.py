#!/usr/bin/env python3
"""Final test - direct MyMemory API (no proxy needed, works out of the box)."""
import sys
import time
import requests
import re

sys.path.insert(0, '.')

def translate_mymemory(text: str, source: str = 'zh', target: str = 'en') -> str:
    """Translate via MyMemory (no API key required, no proxy needed)."""
    try:
        params = {
            'q': text,
            'langpair': f'{source}|{target}',
            'de': 'md-sync-test@test.com'  # dummy email to help with rate limits
        }
        resp = requests.get(
            'https://api.mymemory.translated.net/get',
            params=params,
            timeout=15,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get('responseStatus') == 200:
            return data['responseData']['translatedText']
        else:
            print(f"  [ERROR] MyMemory: {data.get('responseDetails')}")
            return None
    except Exception as e:
        print(f"  [EXCEPTION] {type(e).__name__}: {e}")
        return None

def extract_chinese_blocks(md_content: str):
    """Extract likely Chinese paragraphs for translation testing."""
    # Split by double newline (paragraphs)
    paragraphs = re.split(r'\n\s*\n', md_content.strip())
    chinese_blocks = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # Skip code blocks, HTML tags, frontmatter, etc.
        if (p.startswith('```') or 
            p.startswith('---') or 
            re.match(r'^[\s\-*]+', p) or  # list items
            p.startswith('#') or          # headers
            '[![' in p or                 # images
            'http' in p.lower()):         # URLs
            continue
        # Check if contains Chinese characters
        if re.search(r'[\u4e00-\u9fff]', p):
            chinese_blocks.append(p)
    return chinese_blocks

def main():
    print("=" * 70)
    print("README.md 中文提取 + MyMemory 翻译测试")
    print("=" * 70)
    
    # 1. Load README
    try:
        with open('README.md', 'r', encoding='utf-8') as f:
            content = f.read()
        print(f"✅ 已读取 README.md ({len(content)} 字符)")
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")
        return False
    
    # 2. Extract Chinese blocks
    blocks = extract_chinese_blocks(content)
    print(f"🔍 提取到 {len(blocks)} 个中文段落")
    
    if not blocks:
        print("❌ 未找到中文内容")
        return False
    
    # 3. Test translation on first 5 blocks (to avoid rate limits)
    test_blocks = blocks[:5]
    print(f"\n🧪 测试前 {len(test_blocks)} 个段落:")
    print("-" * 70)
    
    success = 0
    total = len(test_blocks)
    results = []
    
    for i, zh_text in enumerate(test_blocks):
        print(f"\n[{i+1}/{total}] 原文 ({len(zh_text)} 字符):")
        print(f"  {zh_text[:80]}{'...' if len(zh_text) > 80 else ''}")
        
        start = time.perf_counter()
        en_text = translate_mymemory(zh_text, 'zh', 'en')
        elapsed = time.perf_counter() - start
        
        if en_text:
            success += 1
            results.append((zh_text, en_text))
            print(f"  ✅ 译文 ({elapsed*1000:.0f}ms):")
            print(f"  {en_text[:80]}{'...' if len(en_text) > 80 else ''}")
        else:
            print(f"  ❌ 翻译失败 ({elapsed*1000:.0f}ms)")
    
    print("\n" + "=" * 70)
    print(f"📊 结果: {success}/{total} 成功")
    
    if success == total:
        print("🎉 所有测试通过！翻译功能正常。")
        print("\n📝 建议:")
        print("  1. MyMemory 可用作免费翻译后端（无需 API key）")
        print("  2. 可集成到 md-sync 的 translation/fallback.py 作为备选方案")
        print("  3. 对于生产环境，建议使用有 API key 的服务（DeepL/Azure）以获得更好质量")
        return True
    else:
        print("⚠️ 部分测试失败，可能遇到速率限制或网络问题")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
