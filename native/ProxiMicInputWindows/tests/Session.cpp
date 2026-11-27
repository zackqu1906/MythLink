// Compile with the same Windows SDK/compiler as the DLL; no registration or
// target application is needed. Exercise the real OnEndEdit implementation.
#define MYTHLINK_TSF_TEST
#define INITGUID
#include <windows.h>
static HWND testEditor;
static HWND foregroundForTest() { return testEditor ? testEditor : GetForegroundWindow(); }
#define GetForegroundWindow foregroundForTest
#include "../TextService.cpp"
#undef GetForegroundWindow
#include <algorithm>
#include <cassert>
#include <iostream>

namespace {
struct Range final : ITfRangeACP {
    ULONG refs = 1;
    std::wstring& text;
    LONG start, end;
    Range(std::wstring& text, LONG start, LONG end) : text(text), start(start), end(end) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (iid != IID_ITfRangeACP) return E_NOINTERFACE;
        *out = static_cast<ITfRangeACP*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { const auto n = --refs; if (!n) delete this; return n; }
    HRESULT STDMETHODCALLTYPE GetText(TfEditCookie, DWORD flags, WCHAR* buffer, ULONG size, ULONG* count) override {
        *count = text.copy(buffer, (std::min<ULONG>)(size, end - start), start);
        if (flags & TF_TF_MOVESTART) start += *count;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE ShiftStart(TfEditCookie, LONG delta, LONG* moved, const TF_HALTCOND*) override {
        const auto next = (std::max)(0L, start + delta); *moved = next - start; start = next; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE ShiftStartToRange(TfEditCookie, ITfRange* other, TfAnchor) override {
        start = static_cast<Range*>(other)->start; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE IsEmpty(TfEditCookie, BOOL* empty) override { *empty = start == end; return S_OK; }
    HRESULT STDMETHODCALLTYPE Collapse(TfEditCookie, TfAnchor anchor) override {
        start = end = anchor == TF_ANCHOR_START ? start : end; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Clone(ITfRange** out) override { *out = new Range(text, start, end); return S_OK; }
    HRESULT STDMETHODCALLTYPE GetExtent(LONG* offset, LONG* length) override {
        *offset = start; *length = end - start; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetText(TfEditCookie, DWORD flags, const WCHAR* value, LONG length) override {
        assert(flags == TF_ST_CORRECTION || flags == 0);
        text.replace(start, end - start, value, length);
        end = start + length;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetFormattedText(TfEditCookie, IDataObject**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetEmbedded(TfEditCookie, REFGUID, REFIID, IUnknown**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE InsertEmbedded(TfEditCookie, DWORD, IDataObject*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ShiftEnd(TfEditCookie, LONG, LONG*, const TF_HALTCOND*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ShiftEndToRange(TfEditCookie, ITfRange*, TfAnchor) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ShiftStartRegion(TfEditCookie, TfShiftDir, BOOL*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ShiftEndRegion(TfEditCookie, TfShiftDir, BOOL*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE IsEqualStart(TfEditCookie, ITfRange*, TfAnchor, BOOL*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE IsEqualEnd(TfEditCookie, ITfRange*, TfAnchor, BOOL*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE CompareStart(TfEditCookie, ITfRange*, TfAnchor, LONG*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE CompareEnd(TfEditCookie, ITfRange*, TfAnchor, LONG*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE AdjustForInsert(TfEditCookie, ULONG, BOOL*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetGravity(TfGravity*, TfGravity*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SetGravity(TfEditCookie, TfGravity, TfGravity) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetContext(ITfContext**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SetExtent(LONG offset, LONG length) override {
        if (offset < 0 || length < 0 || static_cast<size_t>(offset + length) > text.size()) return E_INVALIDARG;
        start = offset; end = offset + length; return S_OK;
    }
};

struct Composition final : ITfComposition {
    std::wstring& text;
    LONG start;
    LONG& end;
    ULONG refs = 1;
    Composition(std::wstring& text, LONG start, LONG& end) : text(text), start(start), end(end) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void**) override { return E_NOINTERFACE; }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override { const auto n = --refs; if (!n) delete this; return n; }
    HRESULT STDMETHODCALLTYPE GetRange(ITfRange** out) override { *out = new Range(text, start, end); return S_OK; }
    HRESULT STDMETHODCALLTYPE ShiftStart(TfEditCookie, ITfRange*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ShiftEnd(TfEditCookie, ITfRange*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE EndComposition(TfEditCookie) override { return S_OK; }
};

struct Context final : ITfContext, ITfInsertAtSelection, ITfContextComposition {
    bool ownWrite = true;
    HRESULT writerStatus = S_OK;
    TfClientId requestedClient = 0;
    ITfEditSession* pending = nullptr;
    DWORD requestedFlags = 0;
    std::wstring text;
    LONG start = 0, end = 0;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (iid == IID_ITfInsertAtSelection) *out = static_cast<ITfInsertAtSelection*>(this);
        else if (iid == IID_ITfContextComposition) *out = static_cast<ITfContextComposition*>(this);
        else return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return 1; }
    ULONG STDMETHODCALLTYPE Release() override { return 1; }
    HRESULT STDMETHODCALLTYPE RequestEditSession(TfClientId, ITfEditSession* edit, DWORD flags, HRESULT* result) override {
        if (testEditor && !(flags & TF_ES_ASYNC)) {
            *result = edit->DoEditSession(0);
            return S_OK;
        }
        assert(!pending);
        pending = edit;
        pending->AddRef();
        requestedFlags = flags;
        *result = TS_S_ASYNC;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE InWriteSession(TfClientId client, BOOL* own) override {
        requestedClient = client;
        *own = ownWrite;
        return writerStatus;
    }
    HRESULT STDMETHODCALLTYPE GetSelection(TfEditCookie, ULONG, ULONG, TF_SELECTION* sel, ULONG* count) override {
        *sel = TF_SELECTION{new Range(text, start, end), {TF_AE_NONE, FALSE}}; *count = 1; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetSelection(TfEditCookie, ULONG, const TF_SELECTION* sel) override {
        auto* range = static_cast<Range*>(sel->range);
        start = range->start; end = range->end; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetStart(TfEditCookie, ITfRange** out) override {
        *out = new Range(text, 0, 0); return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetEnd(TfEditCookie, ITfRange** out) override {
        const auto size = static_cast<LONG>(text.size());
        *out = new Range(text, size, size); return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetActiveView(ITfContextView**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE EnumViews(IEnumTfContextViews**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetStatus(TF_STATUS* status) override { *status = {}; return S_OK; }
    HRESULT STDMETHODCALLTYPE GetProperty(REFGUID, ITfProperty**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetAppProperty(REFGUID, ITfReadOnlyProperty**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE TrackProperties(const GUID**, ULONG, const GUID**, ULONG, ITfReadOnlyProperty**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE EnumProperties(IEnumTfProperties**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetDocumentMgr(ITfDocumentMgr**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE CreateRangeBackup(TfEditCookie, ITfRange*, ITfRangeBackup**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE InsertTextAtSelection(TfEditCookie, DWORD flags, const WCHAR* value, LONG size,
                                                    ITfRange** out) override {
        if (!(flags & TF_IAS_QUERYONLY)) {
            text.replace(start, end - start, value, size);
            end = start + size;
        }
        *out = new Range(text, start, end);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE InsertEmbeddedAtSelection(TfEditCookie, DWORD, IDataObject*, ITfRange**) override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE StartComposition(TfEditCookie, ITfRange* range, ITfCompositionSink*,
                                             ITfComposition** out) override {
        *out = new Composition(text, static_cast<Range*>(range)->start, end);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE EnumCompositions(IEnumITfCompositionView**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE FindComposition(TfEditCookie, ITfRange*, IEnumITfCompositionView**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE TakeOwnership(TfEditCookie, ITfCompositionView*, ITfCompositionSink*, ITfComposition**) override {
        return E_NOTIMPL;
    }
};

struct Record final : ITfEditRecord {
    BOOL changed = TRUE;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void**) override { return E_NOINTERFACE; }
    ULONG STDMETHODCALLTYPE AddRef() override { return 1; }
    ULONG STDMETHODCALLTYPE Release() override { return 1; }
    HRESULT STDMETHODCALLTYPE GetSelectionStatus(BOOL* result) override { *result = changed; return S_OK; }
    HRESULT STDMETHODCALLTYPE GetTextAndPropertyUpdates(DWORD, const GUID**, ULONG, IEnumTfRanges**) override { return E_NOTIMPL; }
};

struct ServiceTest {
    static void voiceActions() {
        Context context;
        Service service;
        testEditor = CreateWindowExW(0, L"STATIC", L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, nullptr, nullptr);
        assert(testEditor);
        service.context_.reset(&context);
        service.threadFocus_ = service.enabled_ = true;
        service.epoch_ = encode(L"test");
        auto send = [&](const std::string& kind, Fields fields = {}) {
            fields["client_id"] = service.clientId();
            fields["epoch"] = service.epoch_;
            fields["utterance_id"] = encode(L"session");
            fields.try_emplace("revision", "n" + std::to_string(service.revision_));
            service.dispatch(kind, std::move(fields));
        };
        auto begin = [&](Fields options = {}) {
            send("reset");
            context.text = L"明天开会下午三点";
            context.start = 4;
            context.end = 6;  // Replace a selection while dictating; preserve the suffix.
            send("begin", std::move(options));
            assert(service.phase_ == "listening" && service.voicePrefix_ == L"明天开会");
        };
        auto final = [&](const wchar_t* text, const wchar_t* task = L"") {
            send("update", {{"text", encode(text)}, {"final", "b1"}, {"automatic_task", encode(task)}});
        };
        auto convert = [&] {
            begin({{"routed_edit_mode", "b1"}});
            send("update", {{"text", encode(L"改到")}});
            assert(service.canConvertVoice());
            send("convert");
            assert(service.editRequested_ && service.phase_ == "finishing");
            final(L"改到后天");
            assert(service.phase_ == "editing" && context.text == L"明天开会改到后天三点");
        };
        convert();
        send("edit_result", {{"revision", "n0"}, {"text", encode(L"过期")}});
        assert(service.phase_ == "editing" && context.text == L"明天开会改到后天三点");
        send("edit_result", {{"text", encode(L"后天开会")}});
        assert(service.phase_ == "edited" && context.text == L"后天开会下午三点");
        send("cancel");
        assert(service.voiceHistoryResult_ && service.voiceHistoryResult_->phase == "dictated");
        assert(context.text == L"明天开会改到后天三点" && context.start == context.end && service.retryEdit_);
        // Deliver the edit result only if the exact requested prefix still matches.
        convert();
        context.text[0] = L'今';
        send("edit_result", {{"text", encode(L"不应写入")}});
        assert(service.phase_ == "error" && context.text == L"今天开会改到后天三点");
        convert();
        send("cancel");
        send("edit_result", {{"text", encode(L"迟到的结果")}});
        assert(service.phase_ == "dictated" && context.text == L"明天开会改到后天三点");
        begin();
        send("update", {{"text", encode(L"普通听写")}});
        send("convert");
        send("cancel");
        assert(service.phase_ == "finishing" && !service.editRequested_);
        final(L"普通听写");
        assert(service.phase_ == "dictated" && context.text == L"明天开会普通听写三点");
        begin({{"automatic_mode", "b1"}});
        final(L"改写", L"edit");
        assert(service.phase_ == "editing");
        send("edit_result", {{"error", encode(L"模型不可用")}});
        assert(service.phase_ == "dictated" && context.text == L"明天开会改写三点");
        begin();
        final(L"早上");
        send("polish", {{"restore_raw_on_undo", "b1"}});
        assert(service.phase_ == "polishing");
        send("polish_result", {{"text", encode(L"上午")}});
        assert(service.polished_ && context.text == L"明天开会上午三点");
        send("configure", {{"automatic_polish", "b1"}});
        begin();
        final(L"早上");
        assert(!service.previewToken_.empty());
        send("dictation_preview_result", {{"token", service.previewToken_}, {"text", encode(L"上午")}});
        assert(context.text == L"明天开会早上三点" && service.previewText_ == L"上午");
        send("accept_dictation_preview", {{"token", service.previewToken_}});
        assert(context.text == L"明天开会上午三点" && service.previewToken_.empty());
        service.context_.reset();
        DestroyWindow(testEditor);
        testEditor = nullptr;
    }
    static void run() {
        voiceActions();
        Service service;
        Context context;
        Record record;
        service.client_ = 17;
        service.stroke_ = "active-stroke";
        // Model the real TSF order: DoEditSession has already returned, so the
        // lexical writing_ flag is false when each completion is delivered.
        service.writing_ = false;
        for (int index = 0; index < 2; ++index) {
            assert(SUCCEEDED(service.OnEndEdit(&context, 0, &record)));
            assert(context.requestedClient == 17);
            assert(!service.focusDirty_ && service.selection_ == 0);
            assert(service.stroke_ == "active-stroke");
        }
        context.ownWrite = false;
        service.OnEndEdit(&context, 0, &record);
        assert(service.focusDirty_ && service.selection_ == 1);
        service.focusDirty_ = false;
        record.changed = FALSE;
        service.OnEndEdit(&context, 0, &record);
        assert(!service.focusDirty_ && service.selection_ == 1);
        // An API failure cannot suppress a genuine external selection change.
        context.writerStatus = E_FAIL;
        context.ownWrite = true;
        record.changed = TRUE;
        service.OnEndEdit(&context, 0, &record);
        assert(service.focusDirty_ && service.selection_ == 2);

        // A queued ring commit must reject an obsolete editor/session.
        service.context_.reset(&context);
        service.focusDirty_ = false;
        service.epoch_ = "epoch";
        service.commitStroke({{"request_id", "request"}, {"text", encode(L"字")}});
        assert(context.pending && context.requestedFlags == (TF_ES_ASYNC | TF_ES_READWRITE));
        ++service.generation_;
        assert(context.pending->DoEditSession(0) == E_ABORT);
        context.pending->Release();
        context.pending = nullptr;

        context.text = L"前\U0001F600后";
        context.start = context.end = 3;
        context.ownWrite = false;
        context.writerStatus = S_OK;
        Service::EditorSelection before;
        assert(service.editorSelection(0, before) == S_OK);
        const auto deadline = GetTickCount64() + 500;
        service.backspace_ = Service::PendingBackspace{"emoji", before, deadline, false};
        // A caret move alone is not deletion. An editor can publish the
        // moved caret before reporting the new document length.
        context.start = context.end = 1;
        service.OnEndEdit(&context, 0, &record);
        assert(service.backspace_ && service.backspace_->selectionChanged && !service.focusDirty_);
        context.text = L"前后";
        service.OnEndEdit(&context, 0, &record);
        assert(!service.backspace_ && !service.focusDirty_ && service.stroke_ == "active-stroke");
        service.OnEndEdit(&context, 0, &record);
        assert(!service.focusDirty_);
        context.start = context.end = 2;
        service.OnEndEdit(&context, 0, &record);
        assert(service.focusDirty_);

        const Service::EditorSelection beforePrefix{3, 0, 5};
        const Service::EditorSelection afterPrefix{0, 0, 2};
        const Service::EditorSelection afterSingle{2, 0, 4};
        assert(afterPrefix.showsBackspaceFrom(beforePrefix, true));
        assert(!afterSingle.showsBackspaceFrom(beforePrefix, true));
        const auto keys = Service::deletionKeys();
        assert(keys[0].ki.wVk == VK_CONTROL && keys[1].ki.wVk == VK_SHIFT && keys[2].ki.wVk == VK_HOME);
        assert(keys[3].ki.dwFlags & KEYEVENTF_KEYUP);
        assert(keys[4].ki.dwFlags & KEYEVENTF_KEYUP);
        assert(keys[5].ki.dwFlags & KEYEVENTF_KEYUP);
        assert(keys[6].ki.wVk == VK_BACK && keys[6].ki.dwFlags == 0 && keys[7].ki.dwFlags == KEYEVENTF_KEYUP);

        service.focusDirty_ = false;
        service.deleteBefore(true, "delete");
        assert(!context.pending && !service.backspace_);  // No foreground window, no key or selection.
        const std::wstring prefix = L"已有\U0001F600。明天去青华大学。";
        const std::wstring suffix = L"光标后有字\U0001F600";
        const std::wstring handwriting = L"清华 ";
        context.text = prefix + suffix;
        context.start = context.end = static_cast<LONG>(prefix.size());
        ComPtr<ITfRange> caret;
        assert(service.selected(0, caret) == S_OK);
        service.correctionEnabled_ = true;
        auto word = service.correctionBeforeCommit(0, caret.get());
        assert(word && word->preceding.text == prefix && word->inserted.empty());
        context.text = prefix + handwriting + suffix;
        context.start = context.end = static_cast<LONG>(prefix.size() + handwriting.size());
        service.recordCorrectionCommit(0, std::move(word), handwriting);
        assert(service.correctionWord_ && service.correctionWord_->word() == L"清华");

        const auto typed = service.correctionWord_->expected();
        auto corrected = service.correctionWord_->preceding;
        const auto offset = service.correctionOffset(prefix, L"青华");
        assert(offset && !service.correctionOffset(L"哈哈哈", L"哈哈"));
        assert(!service.correctionOffset(prefix, L"清华") && !service.correctionOffset(prefix, L""));
        corrected.text.replace(*offset, 2, L"清华");
        const auto original = context.text;
        bool wrote = false;
        const auto focused = [] { return true; };
        --context.end;
        context.start = context.end;  // Stale caret rejects before any rewrite.
        assert(service.rewriteCorrection(0, typed, corrected, *offset, wrote, focused) == E_ABORT);
        assert(!wrote && context.text == original);
        context.start = context.end = typed.end();
        context.text[0] = L'新';  // A concurrent edit can retain the same caret offset.
        assert(service.rewriteCorrection(0, typed, corrected, *offset, wrote, focused) == E_ABORT);
        assert(!wrote && context.text[0] == L'新');
        context.text = original;
        assert(service.rewriteCorrection(0, typed, corrected, *offset, wrote, [] { return false; }) == E_ABORT);
        assert(!wrote && context.text == original);
        assert(service.rewriteCorrection(0, typed, corrected, *offset, wrote, focused) == S_OK);
        assert(wrote && context.text == corrected.text + suffix);
        assert(context.start == corrected.end() && context.start == context.end);
        wrote = false;
        assert(service.rewriteCorrection(0, corrected, typed, *offset, wrote, focused) == S_OK);
        assert(wrote && context.text == original);
        assert(context.start == typed.end() && context.start == context.end);
        service.clearStroke();
        assert(!service.correctionWord_ && !service.correctionUndo_ && !service.correctionBusy());
        const Service::VoiceChange sentence{1, L"旧词", L"听写\U0001F600", "voice"};
        context.text = L"前" + sentence.dictated + L"后";
        context.start = context.end = 1;
        assert(service.restoreVoiceText(0, sentence, false) == E_ABORT);
        context.start = context.end = 1 + static_cast<LONG>(sentence.dictated.size());
        assert(service.restoreVoiceText(0, sentence, false) == S_OK);
        assert(context.text == L"前旧词后" && context.start == 1 && context.end == 3);
        assert(service.restoreVoiceText(0, sentence, true) == S_OK);
        assert(context.text == L"前" + sentence.dictated + L"后" && context.start == context.end);
        context.text[1] = L'新';
        assert(service.restoreVoiceText(0, sentence, false) == E_ABORT && context.text[1] == L'新');
        service.context_.reset();
        service.stroke_.clear();
        std::cout << "TSF dictation, conversion, polish, preview, undo/redo and stroke regressions passed\n";
    }
};
}

// Load the packaged DLL directly. This verifies the taskbar item through a
// real TSF manager without registering a profile or switching keyboards.
static void checkIndicator(const wchar_t* dllPath) {
    assert(SUCCEEDED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED)));
    HMODULE dll = LoadLibraryW(dllPath);
    assert(dll);
    {
        const auto factoryFor = reinterpret_cast<decltype(&DllGetClassObject)>(
            GetProcAddress(dll, "DllGetClassObject"));
        ComPtr<IClassFactory> factory;
        assert(factoryFor && SUCCEEDED(factoryFor(serviceId, IID_IClassFactory, reinterpret_cast<void**>(factory.out()))));
        ComPtr<ITfTextInputProcessor> service;
        assert(SUCCEEDED(factory->CreateInstance(nullptr, IID_ITfTextInputProcessor, reinterpret_cast<void**>(service.out()))));
        ComPtr<ITfThreadMgrEx> manager;
        assert(SUCCEEDED(CoCreateInstance(CLSID_TF_ThreadMgr, nullptr, CLSCTX_INPROC_SERVER,
                                          IID_ITfThreadMgrEx, reinterpret_cast<void**>(manager.out()))));
        TfClientId client{};
        assert(SUCCEEDED(manager->ActivateEx(&client, TF_TMAE_NOACTIVATETIP)));
        assert(SUCCEEDED(service->Activate(manager.get(), client)));
        ComPtr<ITfLangBarItemMgr> bar;
        assert(SUCCEEDED(manager->QueryInterface(IID_ITfLangBarItemMgr, reinterpret_cast<void**>(bar.out()))));
        ComPtr<ITfLangBarItem> item;
        assert(SUCCEEDED(bar->GetItem(mythlinkInputModeItem, item.out())) && item);
        TF_LANGBARITEMINFO info{};
        assert(SUCCEEDED(item->GetInfo(&info)) && info.clsidService == serviceId &&
               info.dwStyle & TF_LBI_STYLE_SHOWNINTRAY && std::wstring(info.szDescription) == L"MythLink");
        ComPtr<ITfLangBarItemButton> button;
        assert(SUCCEEDED(item->QueryInterface(IID_ITfLangBarItemButton, reinterpret_cast<void**>(button.out()))));
        HICON icon{};
        assert(SUCCEEDED(button->GetIcon(&icon)) && icon);
        DestroyIcon(icon);
        assert(SUCCEEDED(service->Deactivate()));
        assert(FAILED(bar->GetItem(mythlinkInputModeItem, item.out())) || !item);
        manager->Deactivate();
    }
    FreeLibrary(dll);
    CoUninitialize();
    std::cout << "TSF input indicator registered, supplied the MythLink icon and was removed\n";
}

int main(int argc, char** argv) {
    ServiceTest::run();
    if (argc == 2) {
        wchar_t path[MAX_PATH]{};
        assert(MultiByteToWideChar(CP_ACP, 0, argv[1], -1, path, MAX_PATH));
        checkIndicator(path);
    }
}
