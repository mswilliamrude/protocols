# PyZMODEM All-in-One (AIO) Scripts - Status & Handover

## Current State: Verified E2E Success!
We have successfully implemented and compiled the standalone `szaio.py` and `rzaio.py` scripts. The core issues involving the Entra ID Bastion proxy stripping control characters, dropping UTF-8 sequences, and `sudo` PTY line discipline corruption have ALL been completely resolved! Both **sending** (downloads) and **requesting** (uploads) work flawlessly over Bastion.

### Key Fixes Implemented
1. **Middlebox Proxy Evasion:** Implemented `--zdle` argument to override the standard `0x18` (CAN) escape character with a printable ASCII character (like `23` / `#`).
2. **UTF-8 Stream Encoding:** Added `-u` / `--utf8` flags to safely encode the entire data stream into printable ASCII using a Quoted-Printable stream encoder integrated into the PyZMODEM byte-pump. This makes the binary payload 100% invisible and immune to Bastion's UTF-8 filters, which were previously dropping `\xd0` and other high-bit bytes.
3. **PTY Corruption Prevention:** Added `-e` / `--escape` flags to unconditionally escape control characters, allowing transfers to survive `sudo -i` / `use_pty=yes` environments without hanging or stripping.
4. **Clean Aborts:** Added a robust `abort()` method to correctly push the ZMODEM CAN sequences through the UTF-8 encoder, allowing graceful cancelation without hanging the SSH session.

## Usage Guide
To bypass Bastion and PTY restrictions, ALWAYS pair these 4 arguments on both sides:
`-c -e --utf8 --zdle 23`

### 1. Start Local Wrapper (Download from remote)
```bash
python rzaio.py --debug --utf8 --zdle 23 -- ssh user@target_server
```

### 2. Initiate Download (on Remote Server)
```bash
python szaio.py -c -e --utf8 --zdle 23 /path/to/file
```

### 3. Initiate Upload (on Remote Server)
```bash
python szaio.py -c -e --utf8 --zdle 23 --request local_file.txt
```

*(Note: During uploads, the local wrapper progress bar may update in bursts due to SSH PTY buffer blocking as it streams data over the network. This is normal behavior.)*
