#include <stdbool.h>
static int calls = 0;
__declspec(dllexport) int CallCount(void) { return calls; }
__declspec(dllexport) bool DisableInternet(const char *unused) { calls++; return true; }
__declspec(dllexport) int EasyUsb_StartWorking(void) { calls++; return 0; }
