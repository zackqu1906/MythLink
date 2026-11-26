#pragma once
#include <ctfutb.h>
#include <olectl.h>

// MinGW's ctfutb.h defines ITfLangBarItem but omits its button and menu ABI.
#ifndef __ITfLangBarItemButton_INTERFACE_DEFINED__
enum TfLBIClick { TF_LBI_CLK_RIGHT = 1, TF_LBI_CLK_LEFT = 2 };
struct ITfMenu : IUnknown {
    virtual HRESULT STDMETHODCALLTYPE AddMenuItem(UINT, DWORD, HBITMAP, HBITMAP,
                                                  const WCHAR*, ULONG, ITfMenu**) = 0;
};
struct ITfLangBarItemButton : ITfLangBarItem {
    virtual HRESULT STDMETHODCALLTYPE OnClick(TfLBIClick, POINT, const RECT*) = 0;
    virtual HRESULT STDMETHODCALLTYPE InitMenu(ITfMenu*) = 0;
    virtual HRESULT STDMETHODCALLTYPE OnMenuSelect(UINT) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetIcon(HICON*) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetText(BSTR*) = 0;
};
constexpr GUID IID_ITfLangBarItemButton =
    {0x28c7f1d0, 0xde25, 0x11d2, {0xaf, 0xdd, 0x00, 0x10, 0x5a, 0x27, 0x99, 0xb5}};
#define TF_LBI_STYLE_BTN_MENU 0x00020000
#define TF_LBI_STYLE_SHOWNINTRAY 2
#define TF_LBMENUF_CHECKED 1
#endif

#ifdef __MINGW32__
constexpr GUID mythlinkInputModeItem =
    {0x2c77a81e, 0x41cc, 0x4178, {0xa3, 0xa7, 0x5f, 0x8a, 0x98, 0x75, 0x68, 0xe6}};
#else
#include <ctffunc.h>
#define mythlinkInputModeItem GUID_LBI_INPUTMODE
#endif
