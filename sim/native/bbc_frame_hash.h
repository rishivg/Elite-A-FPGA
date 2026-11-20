// Compact acceptance digest for every displayed bitmap field.
#pragma once
#include <cstdint>
#include <openssl/evp.h>
#include <stdexcept>
#include <string>
#include <vector>
class BbcFrameHash {
    EVP_MD_CTX *context = EVP_MD_CTX_new();

  public:
    BbcFrameHash() {
        if (!context)
            throw std::runtime_error("Cannot allocate frame digest");
    }
    ~BbcFrameHash() { EVP_MD_CTX_free(context); }
    BbcFrameHash(const BbcFrameHash &) = delete;
    BbcFrameHash &operator=(const BbcFrameHash &) = delete;
    template <typename Bytes> std::string digest(const Bytes &rgb) {
        if (rgb.size() != 1024 * 512 * 3 || EVP_DigestInit_ex(context, EVP_sha256(), nullptr) != 1)
            throw std::runtime_error("Invalid frame digest input");
        for (unsigned row = 0; row < 248; row++)
            if (EVP_DigestUpdate(context, rgb.data() + row * 1024 * 3, 512 * 3) != 1)
                throw std::runtime_error("Cannot hash bitmap field");
        unsigned size = 0;
        unsigned char bytes[EVP_MAX_MD_SIZE];
        if (EVP_DigestFinal_ex(context, bytes, &size) != 1 || size != 32)
            throw std::runtime_error("Cannot finish frame digest");
        constexpr char hex[] = "0123456789abcdef";
        std::string result(64, '0');
        for (unsigned i = 0; i < 32; i++) {
            result[2 * i] = hex[bytes[i] >> 4];
            result[2 * i + 1] = hex[bytes[i] & 15];
        }
        return result;
    }
};
