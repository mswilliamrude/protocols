// Emits `flags_hex max_block encode_hex` for known capability vectors so the
// Python reference can assert byte-for-byte parity. Not shipped — parity harness.
use pyprotocols_core::capabilities::TransportCapabilities as TC;
fn hex(b: &[u8]) -> String { b.iter().map(|x| format!("{:02x}", x)).collect() }
fn main() {
    let vectors = [
        TC::new(false, false, false, true, 4096, 0),    // legacy
        TC::new(true, true, true, false, 65536, 0),     // all relax, crc off
        TC::new(true, false, false, true, 131072, 0),   // reliable, crc on, 128k
        TC::new(false, true, false, false, 16384, 0),   // integrity, crc off
    ];
    for c in vectors.iter() {
        println!("{}", hex(&c.encode_bytes()));
    }
}
