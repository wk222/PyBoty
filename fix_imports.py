import os
import re

def get_exports(module_path):
    exports = set()
    try:
        with open(module_path, 'r', encoding='utf-8') as f:
            content = f.read()
            matches = re.findall(r'"([^"]+)":\s*\(', content)
            exports.update(matches)
    except Exception as e:
        print(f"Error reading {module_path}: {e}")
    return exports

packages = {
    "core.systems.context": get_exports("core/systems/context/__init__.py"),
    "core.systems.memory": get_exports("core/systems/memory/__init__.py"),
    "core.systems.runtime.session": get_exports("core/systems/runtime/session/__init__.py"),
    "core.assets.tools": get_exports("core/assets/tools/__init__.py"),
}

def process_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(filepath, 'r', encoding='latin-1') as f:
            content = f.read()

    new_content = content
    
    for pkg, exports in packages.items():
        pattern = re.compile(rf"^(\s*)from {pkg}\.[a-zA-Z0-9_]+ import\s+(.*?)(?=\n[^\s]|\Z)", re.MULTILINE | re.DOTALL)
        
        def replacer(match):
            indent = match.group(1)
            imported_str = match.group(2)
            
            cleaned = imported_str.replace('(', '').replace(')', '').replace('\\', '')
            cleaned = re.sub(r'#.*', '', cleaned)
            imports = [i.strip() for i in cleaned.split(',') if i.strip()]
            
            all_exported = True
            for imp in imports:
                base_imp = imp.split(' as ')[0].strip()
                if base_imp not in exports:
                    all_exported = False
                    break
                    
            if all_exported:
                return f"{indent}from {pkg} import {imported_str}"
            else:
                return match.group(0)

        new_content = pattern.sub(replacer, new_content)

    if new_content != content:
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Updated {filepath}")
        except Exception as e:
            print(f"Failed to write {filepath}: {e}")

if __name__ == "__main__":
    for root, dirs, files in os.walk("."):
        if ".git" in root or "__pycache__" in root or "venv" in root or "node_modules" in root:
            continue
        for file in files:
            if file.endswith(".py"):
                process_file(os.path.join(root, file))
