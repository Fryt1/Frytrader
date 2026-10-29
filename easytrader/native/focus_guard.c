/* MIT License -- Frytrader, thread-local activation veto.
 * No account reads, code patching, keyboard/mouse injection or network access.
 * Hook installation is explicitly limited to the selected GUI thread.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

_Static_assert(sizeof(void *) == 4, "Build this guard for the x86 trader");

__declspec(dllexport)
LRESULT CALLBACK FrytraderFocusGuard(int code, WPARAM target, LPARAM detail) {
    if (code == HCBT_ACTIVATE) {
        return 1;
    }
    return CallNextHookEx(NULL, code, target, detail);
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID reserved) {
    (void)module;
    (void)reason;
    (void)reserved;
    return TRUE;
}
