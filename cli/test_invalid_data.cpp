#include <unicode/utypes.h>

// Deliberately invalid embedded package, linked only into the negative-test executable.
extern "C" {
alignas(16) extern const char U_ICUDATA_ENTRY_POINT[64] = {};
}
