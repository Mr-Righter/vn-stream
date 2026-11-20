// Exercise the actual upstream blend kernel with an adversarial legal allocator.
#include <SDL.h>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <vector>
#include "simd/simd.h"

// Ordinary new[] promises no AVX2 alignment. Force the valid 16-byte case.
void *operator new[](std::size_t size) {
    void *base = nullptr;
    if (posix_memalign(&base, 32, size + 32) != 0) throw std::bad_alloc();
    return static_cast<char *>(base) + 16;
}
void operator delete[](void *pointer) noexcept {
    if (pointer) std::free(static_cast<char *>(pointer) - 16);
}
void operator delete[](void *pointer, std::size_t) noexcept { operator delete[](pointer); }

#include "blend-kernel.inc"

int main() {
    for (int width : {1, 3, 4, 7, 8, 9, 31, 327, 1280}) {
        for (int maskWidth : {1, 5, width + 40}) {
            std::vector<Uint32> a(width), b(width), output(width), mask(maskWidth);
            for (int i = 0; i < width; ++i) {
                a[i] = 0xff204080u;
                b[i] = 0xffc0a060u;
            }
            for (std::size_t i = 0; i < mask.size(); ++i) mask[i] = (i * 13) % 256;
            for (Uint32 value : {0u, 1u, 127u, 255u, 256u, 511u}) {
                alphaBlend32(a.data(), b.data(), output.data(), mask.data(), value,
                             0xffffff00u, mask.size(), 3, width);
                for (int i = 0; i < width; ++i) {
                    Uint32 weight = value > (mask[(i + 3) % maskWidth] & 255u) ? value - (mask[(i + 3) % maskWidth] & 255u) : 0;
                    weight = std::min(weight, 255u);
                    Uint32 expected = 0xff000000u;
                    for (int shift : {0, 8, 16}) {
                        int source = (a[i] >> shift) & 255;
                        int target = (b[i] >> shift) & 255;
                        expected |= Uint32(source + ((target - source) * int(weight) >> 8)) << shift;
                    }
                    if (output[i] != expected) {
                        std::fprintf(stderr, "width=%d x=%d mask=%u actual=%08x expected=%08x\n", width, i, value, output[i], expected);
                        return 1;
                    }
                }
            }
        }
    }
    std::puts("masked blend matches scalar reference at all tested SIMD boundaries");
}
