#define UNICODE
#define _UNICODE
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

static LRESULT CALLBACK WindowProc(HWND hwnd, UINT msg, WPARAM w, LPARAM l) {
    return DefWindowProcW(hwnd, msg, w, l);
}

__declspec(dllexport) int RunSpawn(const wchar_t *image, const wchar_t *output) {
    wchar_t command[4096];
    _snwprintf(command, 4096, L"\"%ls\" --child \"%ls\" {#notfullscreen#:1,#fullscreen#:1,#teacher_ip#:#192.0.2.10#,#remote#:#229.1.36.200#}", image, output);
    STARTUPINFOW si = {0};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi = {0};
    if (!CreateProcessW(image, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) return -1;
    WaitForSingleObject(pi.hProcess, 8000);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 1;
}

__declspec(dllexport) DWORD RunUnrelatedIoctl(void) {
    HANDLE h = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    DWORD count = 0;
    DeviceIoControl(h, 0, NULL, 0, NULL, 0, &count, NULL);
    DWORD result = GetLastError();
    CloseHandle(h);
    return result;
}

__declspec(dllexport) DWORD RunKeyboardRequest(DWORD keys) {
    HANDLE h = CreateFileW(L"\\\\.\\OeKbdFilter", GENERIC_READ | GENERIC_WRITE,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return GetLastError();
    DWORD input[4] = {2, keys, 0x005c5b38, 0};
    DWORD count = 99;
    DeviceIoControl(h, 0xb2020, input, sizeof(input), NULL, 0, &count, NULL);
    DWORD result = GetLastError();
    CloseHandle(h);
    return result;
}

__declspec(dllexport) int RunDll(const wchar_t *path, int usb) {
    HMODULE module = LoadLibraryW(path);
    if (!module) return -1;
    int (*count)(void) = (void *)GetProcAddress(module, "CallCount");
    int result;
    if (usb) {
        int (*call)(void) = (void *)GetProcAddress(module, "EasyUsb_StartWorking");
        result = call();
    } else {
        unsigned char (*call)(const char *) = (void *)GetProcAddress(module, "DisableInternet");
        result = call("fixture-only");
    }
    return result * 100 + count();
}

int wmain(int argc, wchar_t **argv) {
    if (argc > 3 && wcscmp(argv[1], L"--child") == 0) {
        FILE *f = _wfopen(argv[2], L"wb");
        if (!f) return 2;
        char buffer[4096];
        WideCharToMultiByte(CP_UTF8, 0, argv[3], -1, buffer, sizeof(buffer), NULL, NULL);
        fputs(buffer, f);
        fclose(f);
        return 0;
    }
    WNDCLASSW cls = {0};
    cls.lpfnWndProc = WindowProc;
    cls.hInstance = GetModuleHandleW(NULL);
    cls.lpszClassName = L"OsEasyAutoOwnedTestWindow";
    RegisterClassW(&cls);
    HWND hwnd = CreateWindowExW(WS_EX_TOPMOST, cls.lpszClassName, L"Owned test fixture", WS_POPUP,
                               -2000, -2000, 640, 400, NULL, NULL, cls.hInstance, NULL);
    ShowWindow(hwnd, SW_SHOWNOACTIVATE);
    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return 0;
}
