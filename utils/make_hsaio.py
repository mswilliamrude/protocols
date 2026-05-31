#!/usr/bin/env python3
import re
import os

def create_aio(source_script, output_script):
    # Core modules in dependency order
    modules = [
        'hslink/const.py',
        'hslink/error.py',
        'hslink/tools.py',
        'hslink/base.py',
        'hslink/protocol/hslink.py'
    ]
    
    all_content = ""
    for mod in modules:
        with open(mod, 'r') as f:
            all_content += f.read() + "\n\n"
            
    with open(source_script, 'r') as f:
        cli_content = f.read()
        
    full_content = all_content + "\n" + cli_content
    
    # 1. Remove relative imports
    full_content = re.sub(r'^from \.+[a-zA-Z0-9_\.]* import .*$', '', full_content, flags=re.MULTILINE)
    full_content = re.sub(r'^from protocol[a-zA-Z0-9_\.]* import .*$', '', full_content, flags=re.MULTILINE)
    
    # 2. Clean up shebangs
    full_content = full_content.replace('#!/usr/bin/env python3\n', '')
    
    header = f"#!/usr/bin/env python3\n# Single-file standalone pure-Python HS/Link implementation ({output_script})\n\n"
    
    with open(output_script, 'w') as f:
        f.write(header + full_content)
        
    os.chmod(output_script, 0o755)
    print(f"Successfully built {output_script}")

if __name__ == '__main__':
    create_aio('hslink/hx.py', 'hxaio.py')
    create_aio('hslink/hr.py', 'hraio.py')
