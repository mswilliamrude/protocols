#!/usr/bin/env python3
import re
import os

def create_aio(source_script, output_script):
    # Core modules in dependency order
    modules = [
        'rzsz/modem/const.py',
        'rzsz/modem/error.py',
        'rzsz/modem/tools.py',
        'rzsz/modem/base.py',
        'rzsz/modem/protocol/zmodem.py'
    ]
    
    all_content = ""
    for mod in modules:
        with open(mod, 'r') as f:
            all_content += f.read() + "\n\n"
            
    with open(source_script, 'r') as f:
        cli_content = f.read()
        
    full_content = all_content + "\n" + cli_content
    
    # 1. Remove relative and absolute imports of internal modules
    full_content = re.sub(r'^from \.+[a-zA-Z0-9_\.]* import .*$', '', full_content, flags=re.MULTILINE)
    full_content = re.sub(r'^from modem[a-zA-Z0-9_\.]* import .*$', '', full_content, flags=re.MULTILINE)
    full_content = re.sub(r'^import modem.*?$', '', full_content, flags=re.MULTILINE)
    
    # 2. Remove sys.path modifications
    full_content = re.sub(r'^sys\.path\.insert.*$', '', full_content, flags=re.MULTILINE)
    full_content = re.sub(r'^# Add the .*? Python path$', '', full_content, flags=re.MULTILINE)
    
    # 3. Strip 'const.' namespace prefixes since everything is flat
    full_content = full_content.replace('const.', '')
    
    # 4. Clean up shebangs
    full_content = full_content.replace('#!/usr/bin/env python3\n', '')
    
    header = f"#!/usr/bin/env python3\n# Single-file standalone PyZMODEM implementation ({output_script})\n\n"
    
    with open(output_script, 'w') as f:
        f.write(header + full_content)
        
    os.chmod(output_script, 0o755)
    print(f"Successfully built {output_script}")

if __name__ == '__main__':
    create_aio('rzsz/sz.py', 'szaio.py')
    create_aio('rzsz/rz.py', 'rzaio.py')
