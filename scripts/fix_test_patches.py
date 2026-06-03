from pathlib import Path

def run():
    count = 0
    test_dir = Path('tests')
    for p in test_dir.rglob('*.py'):
        try:
            # Try utf-8 first
            try:
                content = p.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                # Fallback to ignore errors
                content = p.read_text(encoding='utf-8', errors='ignore')
                
            new_content = content
            # Correct any form of core.assets.apps/agents
            new_content = new_content.replace('core.assets.agents', 'core.systems.agents')
            new_content = new_content.replace('core.assets.apps', 'core.systems.apps')

            if new_content != content:
                p.write_text(new_content, encoding='utf-8')
                print(f"Fixed string references in {p}")
                count += 1
        except Exception as e:
            print(f"Error processing {p}: {e}")
            
    print(f"Total test files fixed: {count}")

if __name__ == '__main__':
    run()
