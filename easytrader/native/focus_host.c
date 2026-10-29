/* MIT License -- Frytrader, short-lived x86 CBT hook host.
 * Receives only window/thread/process identities through a private directory.
 * Exits and unhooks on stop, parent exit, target exit or the hard 150s limit.
 * No window, global hook, trading command, keyboard or mouse input is created.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

static WCHAR directory[1024];

static BOOL path(WCHAR *out, const WCHAR *suffix) {
    if (lstrlenW(directory) + lstrlenW(suffix) + 2 >= 1200) return FALSE;
    lstrcpyW(out, directory);
    lstrcatW(out, L"\\");
    lstrcatW(out, suffix);
    return TRUE;
}

static BOOL signal_ready(void) {
    WCHAR name[1200]; DWORD size = 0;
    if (!path(name, L"ready.txt")) return FALSE;
    HANDLE file = CreateFileW(name, GENERIC_WRITE, FILE_SHARE_READ, NULL,
        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return FALSE;
    BOOL ok = WriteFile(file, "ok", 2, &size, NULL);
    CloseHandle(file);
    return ok && size == 2;
}

void WINAPI guard_entry(void) {
    static WCHAR name[1200], dll[1200];
    static char text[160];
    DWORD size = 0;
    unsigned values[5] = {0}, index = 0;
    DWORD count = GetEnvironmentVariableW(
        L"FRYTRADER_FOCUS_DIRECTORY", directory, 1024);
    if (!count || count >= 1024) ExitProcess(2);
    count = GetEnvironmentVariableW(L"FRYTRADER_FOCUS_DLL", dll, 1200);
    if (!count || count >= 1200 || !path(name, L"target.txt")) ExitProcess(3);
    HANDLE file = CreateFileW(name, GENERIC_READ, FILE_SHARE_READ, NULL,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) ExitProcess(4);
    BOOL ok = ReadFile(file, text, 159, &size, NULL);
    CloseHandle(file);
    if (!ok || !size) ExitProcess(5);
    for (DWORD i = 0; i < size; ++i) {
        if (text[i] >= '0' && text[i] <= '9' && index < 5) {
            unsigned digit = (unsigned)(text[i] - '0');
            if (values[index] > (0xFFFFFFFFU - digit) / 10) ExitProcess(5);
            values[index] = values[index] * 10 + digit;
        } else if (text[i] == ',' && index < 4) {
            ++index;
        } else {
            ExitProcess(5);
        }
    }
    if (index != 4 || !values[0] || !values[2] || !values[3] || !values[4])
        ExitProcess(5);
    HWND window = (HWND)(ULONG_PTR)values[0];
    DWORD pid = 0;
    DWORD thread = GetWindowThreadProcessId(window, &pid);
    if (!thread || thread != values[3] || pid != values[2]) ExitProcess(6);
    HANDLE parent = OpenProcess(SYNCHRONIZE, FALSE, values[4]);
    if (!parent) ExitProcess(9);
    HMODULE module = LoadLibraryW(dll);
    if (!module) { CloseHandle(parent); ExitProcess(7); }
    HOOKPROC callback = (HOOKPROC)GetProcAddress(module, "FrytraderFocusGuard");
    HHOOK hook = callback ? SetWindowsHookExW(WH_CBT, callback, module, thread) : NULL;
    if (!hook) { FreeLibrary(module); CloseHandle(parent); ExitProcess(8); }
    if (!signal_ready()) {
        UnhookWindowsHookEx(hook); FreeLibrary(module);
        CloseHandle(parent); ExitProcess(10);
    }
    path(name, L"stop.txt");
    for (unsigned i = 0; i < 15000; ++i) {
        if (GetFileAttributesW(name) != INVALID_FILE_ATTRIBUTES) break;
        if (WaitForSingleObject(parent, 0) != WAIT_TIMEOUT) break;
        DWORD current_pid = 0;
        if (GetWindowThreadProcessId(window, &current_pid) != thread
                || current_pid != pid) break;
        MSG message;
        while (PeekMessageW(&message, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        Sleep(10);
    }
    BOOL unhooked = UnhookWindowsHookEx(hook);
    FreeLibrary(module);
    CloseHandle(parent);
    ExitProcess(unhooked ? 0 : 11);
}
