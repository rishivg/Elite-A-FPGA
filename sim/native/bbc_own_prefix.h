// Scratch states are produced by the current model and scoped to its process.
#pragma once
#include <filesystem>
#include <stdexcept>
#include <unistd.h>

struct OwnPrefixDirectory {
    std::filesystem::path path;
    OwnPrefixDirectory() {
        path =
            std::filesystem::temp_directory_path() / ("fpgaleet-video-" + std::to_string(getpid()));
        if (!std::filesystem::create_directory(path))
            throw std::runtime_error("video prefix directory already exists");
    }
    ~OwnPrefixDirectory() {
        std::error_code ignored;
        std::filesystem::remove_all(path, ignored);
    }
};
