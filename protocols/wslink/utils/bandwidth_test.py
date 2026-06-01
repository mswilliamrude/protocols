import asyncio
import os
import time

print("Overhead comparison:")
block_size = 4096

overhead_16 = 2
overhead_32 = 4

efficiency_16 = block_size / (block_size + overhead_16)
efficiency_32 = block_size / (block_size + overhead_32)

print(f"16-bit block numbers: {efficiency_16*100:.4f}% efficiency")
print(f"32-bit block numbers: {efficiency_32*100:.4f}% efficiency")
print(f"Difference: {(efficiency_16 - efficiency_32)*100:.4f}%")
