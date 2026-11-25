#pragma once

#include <windows.h>
#include <charconv>
#include <string>
#include <unordered_map>

using Fields = std::unordered_map<std::string, std::string>;

inline std::string encode(std::wstring_view value) {
    const auto size = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()),
                                          nullptr, 0, nullptr, nullptr);
    std::string bytes(size, '\0');
    if (size) WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()),
                                  bytes.data(), size, nullptr, nullptr);
    const char digits[] = "0123456789abcdef";
    std::string result = "s";
    for (unsigned char byte : bytes) {
        result += digits[byte >> 4];
        result += digits[byte & 15];
    }
    return result;
}

inline std::wstring decode(std::string_view value) {
    if (value.empty() || value.front() != 's' || value.size() % 2 != 1) return {};
    std::string bytes;
    for (size_t index = 1; index < value.size(); index += 2) {
        unsigned byte{};
        const auto end = value.data() + index + 2;
        auto parsed = std::from_chars(value.data() + index, end, byte, 16);
        if (parsed.ec != std::errc{} || parsed.ptr != end) return {};
        bytes += static_cast<char>(byte);
    }
    const auto size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(),
                                          static_cast<int>(bytes.size()), nullptr, 0);
    std::wstring result(size, L'\0');
    if (size) MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(),
                                  static_cast<int>(bytes.size()), result.data(), size);
    return result;
}

class Pipe {
    HANDLE handle_ = INVALID_HANDLE_VALUE;

public:
    ~Pipe() { close(); }
    bool connected() const { return handle_ != INVALID_HANDLE_VALUE; }

    void close() {
        if (connected()) CloseHandle(handle_);
        handle_ = INVALID_HANDLE_VALUE;
    }

    bool open() {
        DWORD session{};
        if (!ProcessIdToSessionId(GetCurrentProcessId(), &session)) return false;
        const auto name = L"\\\\.\\pipe\\Mythlink.TSF." + std::to_wstring(session);
        handle_ = CreateFileW(name.c_str(), FILE_READ_DATA | FILE_WRITE_DATA | FILE_WRITE_ATTRIBUTES |
                              READ_CONTROL | SYNCHRONIZE, 0, nullptr, OPEN_EXISTING,
                              SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, nullptr);
        if (!connected()) return false;
        DWORD mode = PIPE_READMODE_MESSAGE | PIPE_NOWAIT;
        if (!SetNamedPipeHandleState(handle_, &mode, nullptr, nullptr)) close();
        return connected() && send("hello", {{"protocol", "n1"}});
    }

    bool send(std::string frame, const Fields& fields) {
        if (!connected()) return false;
        for (const auto& [key, value] : fields) frame += "\t" + key + "=" + value;
        DWORD written{};
        if (WriteFile(handle_, frame.data(), static_cast<DWORD>(frame.size()), &written, nullptr)
                && written == frame.size()) return true;
        close();
        return false;
    }

    bool receive(std::string& type, Fields& fields) {
        DWORD size{};
        if (!connected()) return false;
        // Bytes available can span several messages. Read exactly the next frame.
        if (!PeekNamedPipe(handle_, nullptr, 0, nullptr, nullptr, &size) || size > 8 * 1024 * 1024) {
            close();
            return false;
        }
        if (!size) return false;
        std::string frame(size, '\0');
        DWORD read{};
        if (!ReadFile(handle_, frame.data(), size, &read, nullptr) || !read) {
            close();
            return false;
        }
        frame.resize(read);
        auto next = frame.find('\t');
        type = frame.substr(0, next);
        fields.clear();
        while (next != std::string::npos) {
            const auto start = next + 1;
            next = frame.find('\t', start);
            const auto item = frame.substr(start, next - start);
            const auto equal = item.find('=');
            if (equal != std::string::npos) fields.emplace(item.substr(0, equal), item.substr(equal + 1));
        }
        return true;
    }
};
