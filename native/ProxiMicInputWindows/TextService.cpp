#define INITGUID
#include <windows.h>
#include <msctf.h>
#include <inputscope.h>
#include <textstor.h>
#include <algorithm>
#include <atomic>
#include <array>
#include <functional>
#include <initializer_list>
#include <memory>
#include <optional>
#include <utility>
#include <vector>
#include "Pipe.h"
#include "resource.h"
#include "InputIndicator.h"

namespace {
const CLSID serviceId = {0x40b2749b, 0xc169, 0x4a28, {0x92, 0x1c, 0xc3, 0x36, 0x7b, 0x96, 0x48, 0x2b}};
const GUID profileId = {0xd31dd85f, 0x750d, 0x498b, {0xa2, 0xc3, 0x62, 0x82, 0xdd, 0x4e, 0x75, 0x52}};
constexpr LANGID language = MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED);
constexpr wchar_t description[] = L"MythLink";
#ifdef __MINGW32__
// MinGW's msctf.h omits these Windows SDK category identifiers.
constexpr GUID GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT =
    {0x13a016df, 0x560b, 0x46cd, {0x94, 0x7a, 0x4c, 0x3a, 0xf1, 0xe0, 0xe3, 0x5d}};
constexpr GUID GUID_TFCAT_TIPCAP_SYSTRAYSUPPORT =
    {0x25504fb4, 0x7bab, 0x4bc1, {0x9c, 0x69, 0xcf, 0x81, 0x89, 0x0f, 0x0e, 0xf5}};
#endif
const GUID profileCategories[] = {
    GUID_TFCAT_TIP_KEYBOARD,
    GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT,
    GUID_TFCAT_TIPCAP_SYSTRAYSUPPORT,
};
HMODULE module;
std::atomic<long> objects{};

template<class T> class ComPtr {
    T* ptr_ = nullptr;
public:
    ComPtr() = default;
    ComPtr(const ComPtr&) = delete;
    ComPtr& operator=(const ComPtr&) = delete;
    ~ComPtr() { reset(); }
    void reset(T* value = nullptr) {
        if (value) value->AddRef();
        if (ptr_) ptr_->Release();
        ptr_ = value;
    }
    T** out() { reset(); return &ptr_; }
    T* get() const { return ptr_; }
    T* operator->() const { return ptr_; }
    explicit operator bool() const { return ptr_ != nullptr; }
};

class InputIndicator final : public ITfLangBarItemButton, public ITfSource {
    ULONG refs_ = 1;
    ComPtr<ITfLangBarItemSink> sink_;
public:
    InputIndicator() { ++objects; }
    ~InputIndicator() { --objects; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid == IID_IUnknown || iid == IID_ITfLangBarItem || iid == IID_ITfLangBarItemButton)
            *out = static_cast<ITfLangBarItemButton*>(this);
        else if (iid == IID_ITfSource) *out = static_cast<ITfSource*>(this);
        else return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override { const auto n = --refs_; if (!n) delete this; return n; }
    HRESULT STDMETHODCALLTYPE GetInfo(TF_LANGBARITEMINFO* info) override {
        if (!info) return E_POINTER;
        *info = {serviceId, mythlinkInputModeItem, TF_LBI_STYLE_BTN_MENU | TF_LBI_STYLE_SHOWNINTRAY, 0, L"MythLink"};
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetStatus(DWORD* status) override {
        if (!status) return E_POINTER;
        *status = 0;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Show(BOOL) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetText(BSTR* text) override {
        if (!text) return E_POINTER;
        *text = SysAllocString(description);
        return *text ? S_OK : E_OUTOFMEMORY;
    }
    HRESULT STDMETHODCALLTYPE GetTooltipString(BSTR* text) override { return GetText(text); }
    HRESULT STDMETHODCALLTYPE GetIcon(HICON* icon) override {
        if (!icon) return E_POINTER;
        *icon = static_cast<HICON>(LoadImageW(module, MAKEINTRESOURCEW(IDI_MYTHLINK), IMAGE_ICON,
                                               GetSystemMetrics(SM_CXSMICON), GetSystemMetrics(SM_CYSMICON), 0));
        return *icon ? S_OK : E_FAIL;
    }
    HRESULT STDMETHODCALLTYPE OnClick(TfLBIClick, POINT, const RECT*) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE InitMenu(ITfMenu* menu) override {
        return menu ? menu->AddMenuItem(1, TF_LBMENUF_CHECKED, nullptr, nullptr, description,
                                        static_cast<ULONG>(std::size(description) - 1), nullptr) : E_POINTER;
    }
    HRESULT STDMETHODCALLTYPE OnMenuSelect(UINT) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE AdviseSink(REFIID iid, IUnknown* source, DWORD* cookie) override {
        if (!source || !cookie) return E_POINTER;
        if (iid != IID_ITfLangBarItemSink) return CONNECT_E_CANNOTCONNECT;
        if (sink_) return CONNECT_E_ADVISELIMIT;
        const HRESULT hr = source->QueryInterface(iid, reinterpret_cast<void**>(sink_.out()));
        if (SUCCEEDED(hr)) *cookie = 1;
        return hr;
    }
    HRESULT STDMETHODCALLTYPE UnadviseSink(DWORD cookie) override {
        if (!sink_ || cookie != 1) return CONNECT_E_NOCONNECTION;
        sink_.reset();
        return S_OK;
    }
};

class Edit final : public ITfEditSession {
    ULONG refs_ = 1;
    std::function<HRESULT(TfEditCookie)> work_;
public:
    explicit Edit(std::function<HRESULT(TfEditCookie)> work) : work_(std::move(work)) { ++objects; }
    ~Edit() { --objects; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_ITfEditSession) return E_NOINTERFACE;
        *out = static_cast<ITfEditSession*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override { const auto refs = --refs_; if (!refs) delete this; return refs; }
    HRESULT STDMETHODCALLTYPE DoEditSession(TfEditCookie cookie) override {
        try { return work_(cookie); } catch (...) { return E_FAIL; }
    }
};

class Service final : public ITfTextInputProcessor, public ITfThreadMgrEventSink,
                      public ITfThreadFocusSink, public ITfTextEditSink, public ITfCompositionSink {
#ifdef MYTHLINK_TSF_TEST
    friend struct ServiceTest;
#endif
    ULONG refs_ = 1;
    ComPtr<ITfThreadMgr> manager_;
    ComPtr<ITfLangBarItemMgr> langBar_;
    ComPtr<ITfLangBarItem> indicator_;
    ComPtr<ITfContext> context_;
    ComPtr<ITfComposition> composition_;
    TfClientId client_{};
    DWORD managerSink_ = TF_INVALID_COOKIE, focusSink_ = TF_INVALID_COOKIE, editSink_ = TF_INVALID_COOKIE;
    HWND window_{};
    std::wstring windowClass_;
    HWND focusWindow_{};
    Pipe pipe_;
    std::string epoch_, utterance_, stroke_;
    std::wstring original_;
    LONG voiceStart_ = -1;
    struct VoiceChange {
        LONG start;
        std::wstring original, dictated;
        std::string source;
    };
    std::vector<VoiceChange> voiceUndo_, voiceRedo_;
    bool voiceHistoryPending_ = false, redoEnabled_ = false;
    ULONGLONG voiceHistoryDeadline_ = 0;
    struct HistoryResult { std::string phase, source; std::wstring error; };
    std::optional<HistoryResult> voiceHistoryResult_;
    std::string phase_ = "idle";
    std::wstring raw_;
    std::wstring voiceError_;
    std::wstring preceding_;
    const std::wstring application_ = L"win32:" + std::to_wstring(GetCurrentProcessId());
    unsigned generation_ = 0, selection_ = 0, revision_ = 0;
    LONG selectionStart_ = 0, selectionLength_ = 0;
    bool haveSelection_ = false, writing_ = false, threadFocus_ = false, enabled_ = false;
    bool focusDirty_ = false;
    ULONGLONG reconnectAt_ = 0;
    ULONGLONG deleteEchoUntil_ = 0;
    LONG deleteCaret_ = 0;
    struct EditorSelection {
        LONG start = -1, length = -1, end = -1;

        bool showsBackspaceFrom(const EditorSelection& before, bool toStart) const {
            if (start < 0 || length != 0 || before.start < 0 || before.length < 0) return false;
            if (toStart && start != 0) return false;
            if (!toStart && before.length && start != before.start) return false;
            const auto removed = toStart ? static_cast<long long>(before.start) + before.length :
                before.length ? before.length : before.start - start;
            if (removed <= 0) return false;
            return before.end < 0 || end < 0 || before.end - end == removed;
        }
    };
    struct PendingBackspace {
        std::string requestId;
        EditorSelection before;
        ULONGLONG deadline;
        bool toStart;
        bool selectionChanged = false;
    };
    std::optional<PendingBackspace> backspace_;

    static constexpr LONG correctionPrefixLimit = 4096;
    static constexpr LONG correctionWordLimit = 128;
    struct CorrectionText {
        LONG start;
        std::wstring text;
        LONG end() const { return start + static_cast<LONG>(text.size()); }
    };
    struct CorrectionWord {
        CorrectionText preceding;
        std::wstring inserted;
        CorrectionText expected() const { return {preceding.start, preceding.text + inserted}; }
        std::wstring word() const {
            const auto first = inserted.find_first_not_of(L" \t\r\n\u3000");
            return first == std::wstring::npos ? std::wstring{} :
                inserted.substr(first, inserted.find_last_not_of(L" \t\r\n\u3000") - first + 1);
        }
    };
    struct CorrectionUndo {
        CorrectionWord word;
        CorrectionText corrected;
        size_t offset;
    };
    bool correctionEnabled_ = false, correctionCaretChanged_ = false;
    std::optional<CorrectionWord> correctionWord_;
    std::optional<CorrectionUndo> correctionUndo_;
    std::string correctionRequest_;
    ULONGLONG correctionDeadline_ = 0;
    unsigned correctionRevision_ = 0;

    std::string clientId() const { return "s" + hexId(); }
    std::string hexId() const {
        const auto id = std::to_wstring(GetCurrentThreadId()) + L":" + std::to_wstring(generation_);
        return encode(id).substr(1);
    }
    bool foreground() const {
        DWORD pid{};
        HWND foregroundWindow = GetForegroundWindow();
        return enabled_ && threadFocus_ && GetWindowThreadProcessId(foregroundWindow, &pid)
            == GetCurrentThreadId() && pid == GetCurrentProcessId();
    }
    template<class T> static HRESULT advise(IUnknown* object, REFIID iid, T* sink, DWORD& cookie) {
        ComPtr<ITfSource> source;
        HRESULT hr = object->QueryInterface(IID_ITfSource, reinterpret_cast<void**>(source.out()));
        return FAILED(hr) ? hr : source->AdviseSink(iid, sink, &cookie);
    }
    static void unadvise(IUnknown* object, DWORD& cookie) {
        if (!object || cookie == TF_INVALID_COOKIE) return;
        ComPtr<ITfSource> source;
        if (SUCCEEDED(object->QueryInterface(IID_ITfSource, reinterpret_cast<void**>(source.out()))))
            source->UnadviseSink(cookie);
        cookie = TF_INVALID_COOKIE;
    }
    HRESULT edit(ITfContext* context, DWORD access, std::function<HRESULT(TfEditCookie)> action) {
        auto* request = new Edit(std::move(action));
        HRESULT session = E_FAIL;
        const auto hr = context->RequestEditSession(client_, request, access | TF_ES_SYNC, &session);
        request->Release();
        return FAILED(hr) ? hr : session;
    }
    void refreshContext() {
        ComPtr<ITfDocumentMgr> document;
        ComPtr<ITfContext> next;
        GUITHREADINFO gui{sizeof(gui)};
        if (foreground()) {
            if (SUCCEEDED(manager_->GetFocus(document.out())) && document) document->GetTop(next.out());
            GetGUIThreadInfo(GetCurrentThreadId(), &gui);
        }
        if (!focusDirty_ && next.get() == context_.get() && gui.hwndFocus == focusWindow_) return;
        focusDirty_ = false;
        retire();
        unadvise(context_.get(), editSink_);
        context_.reset(next.get());
        focusWindow_ = gui.hwndFocus;
        if (context_) advise(context_.get(), IID_ITfTextEditSink, static_cast<ITfTextEditSink*>(this), editSink_);
        if (!context_) pipe_.close();
        else if (pipe_.connected()) state();
    }
    bool sensitive(ITfRange* range, TfEditCookie cookie) {
        wchar_t cls[32]{};
        GetClassNameW(focusWindow_, cls, 32);
        if (_wcsicmp(cls, L"Edit") == 0 && (GetWindowLongPtrW(focusWindow_, GWL_STYLE) & ES_PASSWORD))
            return true;
        ComPtr<ITfReadOnlyProperty> property;
        if (FAILED(context_->GetAppProperty(GUID_PROP_INPUTSCOPE, property.out()))) return false;
        VARIANT value;
        VariantInit(&value);
        bool blocked = false;
        if (SUCCEEDED(property->GetValue(cookie, range, &value)) && value.vt == VT_UNKNOWN && value.punkVal) {
            ComPtr<ITfInputScope> scopes;
            if (SUCCEEDED(value.punkVal->QueryInterface(IID_ITfInputScope, reinterpret_cast<void**>(scopes.out())))) {
                InputScope* values = nullptr;
                UINT count = 0;
                if (SUCCEEDED(scopes->GetInputScopes(&values, &count))) {
                    for (UINT index = 0; index < count; ++index)
                        blocked |= values[index] == IS_PASSWORD || values[index] == IS_NUMERIC_PASSWORD
                                   || values[index] == IS_PRIVATE;
                }
                CoTaskMemFree(values);
            }
        }
        VariantClear(&value);
        return blocked;
    }
    HRESULT selected(TfEditCookie cookie, ComPtr<ITfRange>& range) {
        TF_STATUS status{};
        if (FAILED(context_->GetStatus(&status)) || status.dwDynamicFlags & TS_SD_READONLY) return E_ACCESSDENIED;
        TF_SELECTION selection{};
        ULONG count{};
        auto hr = context_->GetSelection(cookie, TF_DEFAULT_SELECTION, 1, &selection, &count);
        if (FAILED(hr) || !count || !selection.range) return E_FAIL;
        range.reset(selection.range);
        selection.range->Release();
        return sensitive(range.get(), cookie) ? E_ACCESSDENIED : S_OK;
    }
    static bool extent(ITfRange* range, LONG& start, LONG& length) {
        ComPtr<ITfRangeACP> acp;
        return SUCCEEDED(range->QueryInterface(IID_ITfRangeACP, reinterpret_cast<void**>(acp.out())))
            && SUCCEEDED(acp->GetExtent(&start, &length));
    }
    HRESULT editorSelection(TfEditCookie cookie, EditorSelection& info) {
        ComPtr<ITfRange> range, end;
        const auto hr = selected(cookie, range);
        if (FAILED(hr)) return hr;
        LONG start{}, length{};
        if (extent(range.get(), start, length)) {
            info.start = start;
            info.length = length;
        }
        if (SUCCEEDED(context_->GetEnd(cookie, end.out())) && end && extent(end.get(), start, length))
            info.end = start;
        return S_OK;
    }
    std::wstring precedingText(TfEditCookie cookie, ITfRange* range) {
        ComPtr<ITfRange> before;
        LONG moved{};
        constexpr LONG limit = 82;  // Max vocabulary word plus a boundary in UTF-16.
        wchar_t text[limit]{};
        ULONG length{};
        if (FAILED(range->Clone(before.out())) || FAILED(before->Collapse(cookie, TF_ANCHOR_START)) ||
            FAILED(before->ShiftStart(cookie, -limit, &moved, nullptr)) ||
            FAILED(before->GetText(cookie, 0, text, limit, &length))) return {};
        // The short read can start in the middle of a surrogate pair.
        if (length && text[0] >= 0xDC00 && text[0] <= 0xDFFF)
            return std::wstring(text + 1, length - 1);
        return std::wstring(text, length);
    }
    bool probe(RECT* caret = nullptr, bool capturePreceding = false) {
        if (!context_ || !foreground()) return false;
        return SUCCEEDED(edit(context_.get(), TF_ES_READ, [&](TfEditCookie cookie) {
            ComPtr<ITfRange> range;
            if (FAILED(selected(cookie, range))) return E_ACCESSDENIED;
            LONG start{}, length{};
            if (extent(range.get(), start, length)) {
                if (haveSelection_ && (start != selectionStart_ || length != selectionLength_)) ++selection_;
                haveSelection_ = true;
                selectionStart_ = start;
                selectionLength_ = length;
            }
            if (capturePreceding) preceding_ = precedingText(cookie, range.get());
            if (caret) {
                ComPtr<ITfContextView> view;
                BOOL clipped{};
                range->Collapse(cookie, TF_ANCHOR_END);
                if (SUCCEEDED(context_->GetActiveView(view.out())) && view)
                    view->GetTextExt(cookie, range.get(), caret, &clipped);
            }
            return S_OK;
        }));
    }
    void reply(std::string type, Fields fields = {}) {
        if (epoch_.empty()) return;
        fields["epoch"] = epoch_;
        fields.try_emplace("client_id", clientId());
        fields.try_emplace("utterance_id", utterance_.empty() ? "s" : utterance_);
        fields.try_emplace("revision", "n" + std::to_string(revision_));
        pipe_.send(std::move(type), fields);
    }
    void state(const std::string& request = {}) {
        RECT caret{};
        const bool showCaret = !stroke_.empty() || !utterance_.empty();
        const bool ready = probe(showCaret ? &caret : nullptr);
        Fields fields{{"application", encode(application_)}, {"phase", encode(std::wstring(phase_.begin(), phase_.end()))},
                      {"ready", ready ? "b1" : "b0"}, {"has_composition", composition_ ? "b1" : "b0"},
                      {"selection", ready ? "a" + std::to_string(selection_) + ",0" : "a-1,-1"}, {"raw", encode(raw_)}};
        if (!request.empty()) fields["request_id"] = request;
        fields["undo_history_depth"] = "n" + std::to_string(voiceUndo_.size());
        fields["can_redo"] = redoEnabled_ && !voiceRedo_.empty() ? "b1" : "b0";
        fields["redo_history_depth"] = "n" + std::to_string(voiceRedo_.size());
        fields["can_cancel"] = phase_ == "listening" || phase_ == "finishing" || !voiceUndo_.empty() ? "b1" : "b0";
        fields["awaiting_readback"] = voiceHistoryPending_ ? "b1" : "b0";
        fields["error"] = encode(voiceError_);
        if (showCaret) {
            fields["caret"] = "a" + std::to_string(caret.left) + "," + std::to_string(caret.top)
                + "," + std::to_string(caret.right) + "," + std::to_string(caret.bottom);
        }
        reply("state", std::move(fields));
    }
    HRESULT replace(const std::wstring& text, bool final, bool cancel = false) {
        if (!context_ || (!cancel && !foreground())) return E_ACCESSDENIED;
        writing_ = true;
        const auto hr = edit(context_.get(), TF_ES_READWRITE, [&](TfEditCookie cookie) {
            ComPtr<ITfRange> range;
            if (!composition_) {
                if (cancel || text.empty()) return S_OK;
                if (FAILED(selected(cookie, range))) return E_ACCESSDENIED;
                ComPtr<ITfInsertAtSelection> insertion;
                auto result = context_->QueryInterface(IID_ITfInsertAtSelection,
                    reinterpret_cast<void**>(insertion.out()));
                if (FAILED(result)) return result;
                if (!final) {
                    result = insertion->InsertTextAtSelection(cookie, TF_IAS_QUERYONLY, nullptr, 0, range.out());
                    if (FAILED(result) || !range) return FAILED(result) ? result : E_FAIL;
                }
                original_.clear();
                LONG start{}, length{};
                voiceStart_ = extent(range.get(), start, length) ? start : -1;
                wchar_t buffer[256];
                ComPtr<ITfRange> read;
                if (FAILED(range->Clone(read.out()))) return E_FAIL;
                ULONG count = 0;
                do {
                    result = read->GetText(cookie, TF_TF_MOVESTART, buffer, 256, &count);
                    if (FAILED(result) || original_.size() > 65536) return E_FAIL;
                    original_.append(buffer, count);
                } while (count);
                if (final) {
                    result = insertion->InsertTextAtSelection(cookie, 0, text.data(),
                        static_cast<LONG>(text.size()), range.out());
                    if (FAILED(result)) return result;
                    LONG writtenStart{}, writtenLength{};
                    if (!range || !extent(range.get(), writtenStart, writtenLength) ||
                        writtenStart != voiceStart_ || writtenLength != static_cast<LONG>(text.size()))
                        voiceStart_ = -1;
                    if (range && SUCCEEDED(range->Collapse(cookie, TF_ANCHOR_END))) {
                        TF_SELECTION caret{range.get(), {TF_AE_NONE, FALSE}};
                        context_->SetSelection(cookie, 1, &caret);
                    }
                    return S_OK;
                }
                ComPtr<ITfContextComposition> service;
                result = context_->QueryInterface(IID_ITfContextComposition,
                    reinterpret_cast<void**>(service.out()));
                if (FAILED(result)) return result;
                result = service->StartComposition(cookie, range.get(), this, composition_.out());
                if (FAILED(result) || !composition_) return FAILED(result) ? result : E_FAIL;
            }
            const auto& value = cancel ? original_ : text;
            auto result = composition_->GetRange(range.out());
            if (FAILED(result)) return result;
            result = range->SetText(cookie, 0, value.data(), static_cast<LONG>(value.size()));
            if (FAILED(result)) return result;
            range->Collapse(cookie, TF_ANCHOR_END);
            TF_SELECTION selection{range.get(), {TF_AE_NONE, FALSE}};
            context_->SetSelection(cookie, 1, &selection);
            if (final) {
                ComPtr<ITfComposition> ending;
                ending.reset(composition_.get());
                result = ending->EndComposition(cookie);
                if (SUCCEEDED(result)) composition_.reset();
            }
            return result;
        });
        writing_ = false;
        return hr;
    }
    void reportDelete(const std::string& requestId, bool toStart, HRESULT hr, bool sent = false) {
        wchar_t error[96]{};
        if (FAILED(hr)) swprintf_s(error, L"文本框未确认删除 (0x%08lX)", static_cast<unsigned long>(hr));
        reply("stroke_delete_result", {{"stroke_session", stroke_}, {"request_id", requestId},
            {"to_start", toStart ? "b1" : "b0"}, {"success", SUCCEEDED(hr) ? "b1" : "b0"},
            {"changed", hr == S_OK ? "b1" : "b0"}, {"sent", sent ? "b1" : "b0"}, {"error", encode(error)}});
    }
    bool strokeEditorHasFocus() {
        if (stroke_.empty() || !context_ || !focusWindow_ || focusDirty_ || !foreground()) return false;
        ComPtr<ITfDocumentMgr> document;
        ComPtr<ITfContext> focused;
        GUITHREADINFO gui{sizeof(gui)};
        return GetGUIThreadInfo(GetCurrentThreadId(), &gui) && gui.hwndFocus == focusWindow_ &&
            SUCCEEDED(manager_->GetFocus(document.out())) && document &&
            SUCCEEDED(document->GetTop(focused.out())) && focused.get() == context_.get();
    }
    bool correctionBusy() const { return correctionDeadline_ != 0; }
    void resetCorrection() {
        correctionWord_.reset();
        correctionUndo_.reset();
        correctionRequest_.clear();
        correctionDeadline_ = 0;
        ++correctionRevision_;
    }
    void publishCorrection(const std::wstring& message = {}, const std::wstring* context = nullptr) {
        Fields fields{{"stroke_session", stroke_},
            {"available", correctionEnabled_ ? "b1" : "b0"},
            {"busy", correctionBusy() ? "b1" : "b0"},
            {"can_undo", correctionUndo_ && !correctionBusy() ? "b1" : "b0"},
            {"text", encode(correctionWord_ ? correctionWord_->word() : L"")}, {"message", encode(message)}};
        if (context) fields["context"] = encode(*context);
        reply("stroke_correction_state", std::move(fields));
    }
    static std::optional<CorrectionText> readCorrectionText(TfEditCookie cookie, ITfRange* caret,
                                                             LONG start, ComPtr<ITfRange>& range) {
        LONG position = 0, length = 0;
        if (!extent(caret, position, length) || length != 0 || start < 0 || position < start ||
                position - start > correctionPrefixLimit + 2 * correctionWordLimit ||
                FAILED(caret->Clone(range.out()))) return std::nullopt;
        const LONG size = position - start;
        if (size == 0) return CorrectionText{start, {}};
        LONG moved = 0, actualStart = 0, actualLength = 0;
        if (FAILED(range->ShiftStart(cookie, -size, &moved, nullptr)) || moved != -size ||
                !extent(range.get(), actualStart, actualLength) || actualStart != start || actualLength != size)
            return std::nullopt;
        std::wstring text(size, L'\0');
        ULONG read = 0;
        if (FAILED(range->GetText(cookie, 0, text.data(), size, &read)) || read != static_cast<ULONG>(size))
            return std::nullopt;
        return CorrectionText{start, std::move(text)};
    }
    static bool correctionMatches(TfEditCookie cookie, ITfRange* caret, const CorrectionText& expected,
                                  ComPtr<ITfRange>& range) {
        const auto actual = readCorrectionText(cookie, caret, expected.start, range);
        return actual && actual->text == expected.text;
    }
    std::optional<CorrectionWord> correctionBeforeCommit(TfEditCookie cookie, ITfRange* caret) {
        if (!correctionEnabled_) return std::nullopt;
        ComPtr<ITfRange> range;
        if (correctionWord_ && correctionMatches(cookie, caret, correctionWord_->expected(), range))
            return correctionWord_;
        LONG position = 0, length = 0;
        if (!extent(caret, position, length) || length != 0 || position < 0) return std::nullopt;
        auto preceding = readCorrectionText(cookie, caret, (std::max)(0L, position - correctionPrefixLimit), range);
        if (!preceding) return std::nullopt;
        // The fixed read window can land inside a UTF-16 surrogate pair.
        if (!preceding->text.empty() && preceding->text.front() >= 0xDC00 && preceding->text.front() <= 0xDFFF) {
            preceding->text.erase(0, 1);
            ++preceding->start;
        }
        return CorrectionWord{std::move(*preceding), {}};
    }
    void recordCorrectionCommit(TfEditCookie cookie, std::optional<CorrectionWord> before, const std::wstring& text) {
        if (!correctionEnabled_) return;
        resetCorrection();
        if (before && before->inserted.size() + text.size() <= correctionWordLimit) {
            before->inserted += text;
            ComPtr<ITfRange> caret, range;
            if (SUCCEEDED(selected(cookie, caret)) && correctionMatches(cookie, caret.get(), before->expected(), range)) {
                correctionWord_ = std::move(before);
                publishCorrection();
                return;
            }
        }
        publishCorrection(L"无法核对手写位置或手写词过长");
    }
    bool currentCorrectionText(const CorrectionText& expected) {
        if (!strokeEditorHasFocus()) return false;
        return SUCCEEDED(edit(context_.get(), TF_ES_READ, [&](TfEditCookie cookie) -> HRESULT {
            ComPtr<ITfRange> caret, range;
            return SUCCEEDED(selected(cookie, caret)) && correctionMatches(cookie, caret.get(), expected, range)
                ? S_OK : E_ABORT;
        })) && strokeEditorHasFocus();
    }
    void requestCorrection(const std::string& requestId) {
        if (!correctionEnabled_ || correctionBusy() || backspace_ || requestId.size() < 2 ||
                requestId.front() != 's' || requestId.size() > 257) return;
        if (!correctionWord_ || correctionWord_->word().empty()) {
            publishCorrection(L"请先手写并确认要修正的词");
            return;
        }
        if (correctionWord_->preceding.text.empty()) {
            publishCorrection(L"光标前没有可修正的文本");
            return;
        }
        const auto expected = correctionWord_->expected();
        const auto version = correctionRevision_;
        const bool matches = currentCorrectionText(expected);
        if (correctionRevision_ != version) return;
        if (!matches) {
            resetCorrection();
            publishCorrection(L"前文或光标已变化，未执行修正");
            return;
        }
        const auto word = correctionWord_->word();
        correctionRequest_ = requestId;
        correctionDeadline_ = GetTickCount64() + 30000;
        publishCorrection(L"正在修正「" + word + L"」");
        reply("stroke_correction_requested", {{"stroke_session", stroke_}, {"request_id", requestId},
            {"original", encode(correctionWord_->preceding.text)}, {"handwriting", encode(word)}});
    }
    static std::optional<size_t> correctionOffset(const std::wstring& preceding, const std::wstring& fragment) {
        if (fragment.empty() || fragment.size() > correctionWordLimit) return std::nullopt;
        const auto offset = preceding.find(fragment);
        if (offset == std::wstring::npos || preceding.find(fragment, offset + 1) != std::wstring::npos)
            return std::nullopt;
        return offset;
    }
    std::optional<LONG> documentEnd(TfEditCookie cookie) {
        ComPtr<ITfRange> range;
        LONG start = 0, length = 0;
        if (FAILED(context_->GetEnd(cookie, range.out())) || !range ||
                !extent(range.get(), start, length) || length != 0) return std::nullopt;
        return start;
    }
    HRESULT rewriteCorrection(TfEditCookie cookie, const CorrectionText& before, const CorrectionText& after,
                               size_t offset, bool& wrote, const std::function<bool()>& owns) {
        ComPtr<ITfRange> caret, range;
        const auto end = documentEnd(cookie);
        if (!end || *end < before.end() || FAILED(selected(cookie, caret)) ||
                !correctionMatches(cookie, caret.get(), before, range)) return E_ABORT;
        LONG moved = 0;
        const auto distance = static_cast<LONG>(offset);
        if (distance && (FAILED(range->ShiftStart(cookie, distance, &moved, nullptr)) || moved != distance))
            return E_ABORT;
        LONG start = 0, length = 0;
        if (!extent(range.get(), start, length) || start != before.start + distance ||
                length != before.end() - start || !owns()) return E_ABORT;
        writing_ = true;
        auto hr = range->SetText(cookie, TF_ST_CORRECTION, after.text.data() + offset,
                                 static_cast<LONG>(after.text.size() - offset));
        writing_ = false;
        if (FAILED(hr)) return hr;
        wrote = true;
        const auto newEnd = documentEnd(cookie);
        if (!newEnd || *newEnd - after.end() != *end - before.end()) return E_FAIL;
        hr = range->Collapse(cookie, TF_ANCHOR_END);
        if (FAILED(hr) || !extent(range.get(), start, length) || start != after.end() || length != 0) {
            ComPtr<ITfRangeACP> acp;
            if (FAILED(range->QueryInterface(IID_ITfRangeACP, reinterpret_cast<void**>(acp.out()))) ||
                    FAILED(acp->SetExtent(after.end(), 0))) return E_FAIL;
        }
        if (!owns()) return E_ABORT;
        TF_SELECTION selection{range.get(), {TF_AE_NONE, FALSE}};
        writing_ = true;
        hr = context_->SetSelection(cookie, 1, &selection);
        writing_ = false;
        if (FAILED(hr)) return hr;
        return SUCCEEDED(selected(cookie, caret)) && correctionMatches(cookie, caret.get(), after, range)
            ? S_OK : E_FAIL;
    }
    void queueCorrectionWrite(CorrectionUndo action, bool undo, const std::wstring& message) {
        correctionRequest_.clear();
        if (!strokeEditorHasFocus()) {
            resetCorrection();
            publishCorrection(L"输入焦点已变化，未改动文本");
            return;
        }
        const auto version = ++correctionRevision_;
        correctionDeadline_ = GetTickCount64() + 4000;
        publishCorrection(L"正在写回修正结果");
        AddRef();
        const auto lifetime = std::shared_ptr<Service>(this, [](Service* service) { service->Release(); });
        auto* request = new Edit([this, lifetime, version, undo, action = std::move(action), message](TfEditCookie cookie) {
            if (correctionRevision_ != version) return E_ABORT;
            const auto owns = [&] {
                return correctionRevision_ == version && strokeEditorHasFocus();
            };
            if (!owns() || GetTickCount64() >= correctionDeadline_) {
                resetCorrection();
                publishCorrection(L"输入焦点已变化或写入超时");
                return E_ABORT;
            }
            const auto typed = action.word.expected();
            bool wrote = false;
            const auto hr = rewriteCorrection(cookie, undo ? action.corrected : typed,
                                               undo ? typed : action.corrected, action.offset, wrote, owns);
            if (correctionRevision_ != version) return E_ABORT;
            resetCorrection();
            if (SUCCEEDED(hr)) {
                if (undo) correctionWord_ = action.word;
                else correctionUndo_ = action;
                ComPtr<ITfRange> caret;
                const auto context = SUCCEEDED(selected(cookie, caret)) ? precedingText(cookie, caret.get()) : L"";
                publishCorrection(message, &context);
            } else {
                const std::wstring unknownContext;
                publishCorrection(wrote ? L"未确认写回结果，请检查文本" :
                    hr == E_ABORT ? L"前文或光标已变化，未改动文本" : L"当前文本框拒绝 TSF 写回",
                    wrote ? &unknownContext : nullptr);
            }
            correctionCaretChanged_ = wrote;
            return hr;
        });
        HRESULT result = E_FAIL;
        const auto hr = context_->RequestEditSession(client_, request, TF_ES_ASYNC | TF_ES_READWRITE, &result);
        request->Release();
        if (correctionRevision_ == version && (FAILED(hr) || FAILED(result))) {
            resetCorrection();
            publishCorrection(L"当前文本框未授予 TSF 写入会话");
        }
    }
    void applyCorrection(const Fields& fields) {
        if (correctionRequest_.empty() || fields.at("request_id") != correctionRequest_ || !correctionWord_) return;
        if (GetTickCount64() >= correctionDeadline_) { pollCorrection(); return; }
        const auto error = decode(fields.at("error"));
        const auto fragment = decode(fields.at("original_text"));
        const auto offset = correctionOffset(correctionWord_->preceding.text, fragment);
        if (!error.empty() || !offset) {
            correctionRequest_.clear();
            correctionDeadline_ = 0;
            publishCorrection(error.empty() ? L"未找到唯一的对应片段，已保留手写内容" : error);
            return;
        }
        auto corrected = correctionWord_->preceding;
        const auto word = correctionWord_->word();
        corrected.text.replace(*offset, fragment.size(), word);
        queueCorrectionWrite({*correctionWord_, std::move(corrected), *offset}, false,
                             fragment + L" → " + word + L" · 左滑撤销");
    }
    void pollCorrection() {
        if (!correctionBusy() || GetTickCount64() < correctionDeadline_) return;
        const bool waitingForModel = !correctionRequest_.empty();
        ++correctionRevision_;
        correctionRequest_.clear();
        correctionDeadline_ = 0;
        publishCorrection(waitingForModel ? L"修正超时，已保留手写内容" : L"TSF 写入会话超时，请检查文本");
    }
    void finishBackspace(HRESULT hr, bool sent = true) {
        const auto request = std::move(*backspace_);
        backspace_.reset();
        reportDelete(request.requestId, request.toStart, hr, sent);
    }
    HRESULT checkBackspaceTarget() {
        if (!strokeEditorHasFocus()) return E_ABORT;
        for (const auto key : {VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_HOME, VK_BACK})
            if (GetAsyncKeyState(key) & 0x8000) return HRESULT_FROM_WIN32(ERROR_BUSY);
        return S_OK;
    }
    static INPUT keyboardInput(WORD key, DWORD flags = 0) {
        INPUT input{};
        input.type = INPUT_KEYBOARD;
        input.ki.wVk = key;
        input.ki.dwFlags = flags;
        return input;
    }
    static std::array<INPUT, 8> deletionKeys() {
        // Prefix selection and Backspace belong to the same input batch.
        // The prefix uses the editor's Ctrl+Shift+Home, which preserves the suffix.
        return {keyboardInput(VK_CONTROL), keyboardInput(VK_SHIFT),
                keyboardInput(VK_HOME, KEYEVENTF_EXTENDEDKEY),
                keyboardInput(VK_HOME, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP),
                keyboardInput(VK_SHIFT, KEYEVENTF_KEYUP), keyboardInput(VK_CONTROL, KEYEVENTF_KEYUP),
                keyboardInput(VK_BACK), keyboardInput(VK_BACK, KEYEVENTF_KEYUP)};
    }
    void sendBackspace(bool toStart, const EditorSelection& before, const std::string& requestId) {
        const auto hr = checkBackspaceTarget();
        if (FAILED(hr)) return reportDelete(requestId, toStart, hr);
        // WeChat can expose an empty TSF range for a populated editor.
        // Use the range for confirmation, never to skip the requested key.
        auto keys = deletionKeys();
        const UINT first = toStart ? 0 : 6;
        const UINT count = static_cast<UINT>(keys.size()) - first;
        backspace_ = PendingBackspace{requestId, before, GetTickCount64() + 500, toStart};
        const UINT sent = SendInput(count, keys.data() + first, sizeof(INPUT));
        if (sent != count) {
            // Release any synthetic keys left down after a partial send.
            const UINT stop = first + sent;
            INPUT release[4]{};
            UINT n = 0;
            if (first == 0 && stop == 3) release[n++] = keys[3];
            if (first == 0 && stop >= 2 && stop <= 4) release[n++] = keys[4];
            if (first == 0 && stop >= 1 && stop <= 5) release[n++] = keys[5];
            if (stop == 7) release[n++] = keys[7];
            if (n) SendInput(n, release, sizeof(INPUT));
            if (stop < 7) finishBackspace(E_ACCESSDENIED, false);
        }
    }
    bool confirmBackspace(const EditorSelection& after) {
        if (!backspace_ || !after.showsBackspaceFrom(backspace_->before, backspace_->toStart)) return false;
        deleteCaret_ = after.start;
        deleteEchoUntil_ = GetTickCount64() + 500;
        finishBackspace(S_OK);
        return true;
    }
    void pollBackspace() {
        if (!backspace_) return;
        if (strokeEditorHasFocus() && GetTickCount64() < backspace_->deadline) {
            EditorSelection after;
            const auto hr = edit(context_.get(), TF_ES_READ, [&](TfEditCookie cookie) {
                return editorSelection(cookie, after);
            });
            if (SUCCEEDED(hr)) confirmBackspace(after);
            return;
        }
        if (backspace_->selectionChanged) focusDirty_ = true;
        finishBackspace(HRESULT_FROM_WIN32(ERROR_TIMEOUT));
    }
    void deleteBefore(bool toStart, const std::string& requestId) {
        if (backspace_ || correctionBusy()) return reportDelete(requestId, toStart, HRESULT_FROM_WIN32(ERROR_BUSY));
        const auto target = checkBackspaceTarget();
        if (FAILED(target)) return reportDelete(requestId, toStart, target);
        deleteEchoUntil_ = 0;
        const auto generation = generation_;
        const auto epoch = epoch_;
        const auto stroke = stroke_;
        EditorSelection before;
        const auto hr = edit(context_.get(), TF_ES_READ, [&](TfEditCookie cookie) {
            return editorSelection(cookie, before);
        });
        if (generation_ != generation || epoch_ != epoch || stroke_ != stroke || backspace_) return;
        if (FAILED(hr)) return reportDelete(requestId, toStart, hr);
        if (correctionEnabled_) {
            resetCorrection();
            publishCorrection();
        }
        sendBackspace(toStart, before, requestId);
    }
    bool voiceFinished() const { return phase_ == "dictated" || phase_ == "undone" || phase_ == "error"; }
    void settle(std::string phase, std::wstring error = {}, const std::string& source = {}) {
        phase_ = std::move(phase);
        voiceError_ = error;
        state();
        reply("settled", {{"phase", encode(std::wstring(phase_.begin(), phase_.end()))},
                          {"source_utterance_id", source.empty() ? utterance_ : source},
                          {"error", encode(error)}, {"text", encode(phase_ == "dictated" ? raw_ : L"")}});
    }
    HRESULT restoreVoiceText(TfEditCookie cookie, const VoiceChange& change, bool redo) {
        const auto& before = redo ? change.original : change.dictated;
        const auto& after = redo ? change.dictated : change.original;
        ComPtr<ITfRange> range;
        LONG start{}, length{};
        if (FAILED(selected(cookie, range)) || !extent(range.get(), start, length)) return E_ABORT;
        const LONG size = static_cast<LONG>(before.size());
        if (redo) {
            if (start != change.start || length != size) return E_ABORT;
        } else {
            LONG moved{};
            if (start != change.start + size || length != 0 ||
                FAILED(range->ShiftStart(cookie, -size, &moved, nullptr)) || moved != -size) return E_ABORT;
        }
        std::wstring actual(before.size(), L'\0');
        ULONG count{};
        if (size && (FAILED(range->GetText(cookie, 0, actual.data(), size, &count)) ||
                     count != before.size() || actual != before)) return E_ABORT;
        auto hr = range->SetText(cookie, TF_ST_CORRECTION, after.data(), static_cast<LONG>(after.size()));
        if (FAILED(hr)) return hr;
        if (redo && FAILED(range->Collapse(cookie, TF_ANCHOR_END))) return E_FAIL;
        TF_SELECTION selection{range.get(), {TF_AE_NONE, FALSE}};
        return context_->SetSelection(cookie, 1, &selection);
    }
    void requestVoiceHistory(bool redo) {
        auto& history = redo ? voiceRedo_ : voiceUndo_;
        if (voiceHistoryPending_ || history.empty() || (redo && !redoEnabled_)) return;
        const auto generation = generation_;
        const auto sentence = utterance_;
        const auto revision = ++revision_;
        voiceHistoryPending_ = true;
        voiceHistoryDeadline_ = GetTickCount64() + 2400;
        AddRef();
        const auto lifetime = std::shared_ptr<Service>(this, [](Service* self) { self->Release(); });
        auto* request = new Edit([this, lifetime, generation, sentence, revision, redo](TfEditCookie cookie) {
            if (generation != generation_ || sentence != utterance_ || revision != revision_) return E_ABORT;
            auto& from = redo ? voiceRedo_ : voiceUndo_;
            if (from.empty() || focusDirty_ || !foreground() || GetTickCount64() >= voiceHistoryDeadline_) return E_ABORT;
            auto change = from.back();
            writing_ = true;
            const auto hr = restoreVoiceText(cookie, change, redo);
            writing_ = false;
            if (FAILED(hr)) {
                voiceUndo_.clear();
                voiceRedo_.clear();
                voiceHistoryResult_ = HistoryResult{"error", {}, L"文字或光标已变化，无法恢复本句"};
                return hr;
            }
            from.pop_back();
            if (redo || redoEnabled_) (redo ? voiceUndo_ : voiceRedo_).push_back(change);
            raw_ = redo ? change.dictated : L"";
            voiceHistoryResult_ = HistoryResult{redo ? "dictated" : "undone", change.source, {}};
            return S_OK;
        });
        HRESULT session = E_FAIL;
        const auto hr = context_->RequestEditSession(client_, request, TF_ES_ASYNCDONTCARE | TF_ES_READWRITE, &session);
        request->Release();
        if (!voiceHistoryResult_ && (FAILED(hr) || FAILED(session))) {
            voiceHistoryPending_ = false;
            settle("error", L"输入框未允许修改本句");
        }
    }
    void clearStroke() {
        resetCorrection();
        correctionEnabled_ = correctionCaretChanged_ = false;
        backspace_.reset();
        deleteEchoUntil_ = 0;
        if (stroke_.empty()) return;
        reply("stroke_cleared", {{"stroke_session", stroke_}});
        stroke_.clear();
    }
    void commitStroke(const Fields& fields) {
        const auto requestId = fields.at("request_id");
        const auto text = decode(fields.at("text"));
        const auto prefix = fields.find("word_prefix");
        const auto wordPrefix = prefix == fields.end() ? std::wstring{} : decode(prefix->second);
        const auto generation = generation_;
        const auto epoch = epoch_;
        const auto stroke = stroke_;
        const auto deadline = GetTickCount64() + 4000;
        const auto report = [this, requestId, epoch, stroke, generation](
                HRESULT hr, const wchar_t* stage, const std::wstring* context = nullptr) {
            if (epoch_ != epoch || stroke_ != stroke || generation_ != generation) return;
            wchar_t error[96]{};
            if (FAILED(hr)) swprintf_s(error, L"%ls (0x%08lX)", stage, static_cast<unsigned long>(hr));
            Fields result{{"stroke_session", stroke}, {"request_id", requestId},
                          {"success", SUCCEEDED(hr) ? "b1" : "b0"}, {"error", encode(error)}};
            if (context) result["context"] = encode(*context);
            reply("stroke_result", std::move(result));
        };
        AddRef();
        const auto lifetime = std::shared_ptr<Service>(this, [](Service* service) { service->Release(); });
        auto* request = new Edit([this, lifetime, text, wordPrefix, generation, epoch, stroke, deadline, report](TfEditCookie cookie) {
            if (generation_ != generation || epoch_ != epoch || stroke_ != stroke) return E_ABORT;
            if (correctionBusy()) {
                report(E_ABORT, L"请等待手写修正结束");
                return E_ABORT;
            }
            ComPtr<ITfDocumentMgr> document;
            ComPtr<ITfContext> focused;
            if (focusDirty_ || !foreground() || GetTickCount64() >= deadline ||
                FAILED(manager_->GetFocus(document.out())) || !document ||
                FAILED(document->GetTop(focused.out())) || focused.get() != context_.get()) {
                report(E_ABORT, L"输入焦点已改变或写入超时");
                return E_ABORT;
            }
            ComPtr<ITfRange> range;
            auto hr = text.empty() ? E_INVALIDARG : selected(cookie, range);
            if (FAILED(hr)) { report(hr, L"无法确认可写选区"); return hr; }
            if (!wordPrefix.empty()) {
                auto previous = precedingText(cookie, range.get());
                if (previous.size() < wordPrefix.size() ||
                    previous.compare(previous.size() - wordPrefix.size(), wordPrefix.size(), wordPrefix) != 0) {
                    report(E_ABORT, L"词头已改变，请重新选择");
                    return E_ABORT;
                }
            }
            ComPtr<ITfInsertAtSelection> insertion;
            hr = focused->QueryInterface(IID_ITfInsertAtSelection, reinterpret_cast<void**>(insertion.out()));
            if (FAILED(hr)) { report(hr, L"文本框不支持 TSF 插入"); return hr; }
            auto correctionBefore = correctionBeforeCommit(cookie, range.get());
            writing_ = true;
            hr = insertion->InsertTextAtSelection(cookie, 0, text.data(),
                                                   static_cast<LONG>(text.size()), range.out());
            const bool hasCaret = SUCCEEDED(hr) && range && SUCCEEDED(range->Collapse(cookie, TF_ANCHOR_END));
            std::wstring context;
            if (hasCaret) {
                TF_SELECTION caret{range.get(), {TF_AE_NONE, FALSE}};
                focused->SetSelection(cookie, 1, &caret);
                context = precedingText(cookie, range.get());
            }
            writing_ = false;
            if (SUCCEEDED(hr)) recordCorrectionCommit(cookie, std::move(correctionBefore), text);
            report(hr, L"TSF 插入候选字失败", hasCaret ? &context : nullptr);
            return hr;
        });
        HRESULT session = E_FAIL;
        // Ring commands arrive on a timer, outside TSF's keyboard callback.
        const auto hr = context_->RequestEditSession(client_, request, TF_ES_ASYNC | TF_ES_READWRITE, &session);
        request->Release();
        if (FAILED(hr) || FAILED(session)) report(FAILED(hr) ? hr : session, L"TSF 未授予写入会话");
    }
    void retire() {
        if (composition_) replace(L"", true, true);
        composition_.reset();
        clearStroke();
        if (!utterance_.empty() && !voiceFinished())
            reply("interrupted", {{"reason", encode(L"输入框已改变，本句已取消")}});
        voiceUndo_.clear();
        voiceRedo_.clear();
        voiceHistoryPending_ = false;
        voiceHistoryResult_.reset();
        voiceError_.clear();
        utterance_.clear();
        phase_ = "idle";
        raw_.clear();
        haveSelection_ = false;
        ++generation_;
    }
    void dispatch(const std::string& type, Fields fields) {
        if (type == "welcome") {
            epoch_ = fields["epoch"];
            state();
            return;
        }
        if (epoch_.empty() || fields["epoch"] != epoch_) return;
        if (type == "configure") {
            redoEnabled_ = fields["redo_enabled"] == "b1";
            if (!redoEnabled_) voiceRedo_.clear();
            return;
        }
        if (type == "ping") { state(fields["request_id"]); return; }
        if (fields["client_id"] != clientId()) return;
        if (type == "begin") {
            if ((!utterance_.empty() && !voiceFinished()) || voiceHistoryPending_ || !stroke_.empty() || !probe()) return;
            const auto expected = fields["selection_revision"];
            if (!expected.empty() && expected != "n" + std::to_string(selection_)) return;
            utterance_ = fields["utterance_id"];
            voiceStart_ = -1;
            original_.clear();
            raw_.clear();
            voiceError_.clear();
            ++revision_;
            phase_ = "listening";
            state();
        } else if (type == "stroke_begin") {
            RECT caret{};
            const bool ok = (utterance_.empty() || voiceFinished()) && !voiceHistoryPending_ &&
                probe(&caret, true) && caret.bottom > caret.top;
            if (ok) {
                utterance_.clear();
                raw_.clear();
                voiceUndo_.clear();
                voiceRedo_.clear();
                voiceError_.clear();
                phase_ = "idle";
                resetCorrection();
                stroke_ = fields["stroke_session"];
                correctionEnabled_ = fields["correction_enabled"] == "b1";
            }
            reply("stroke_ready", {{"stroke_session", fields["stroke_session"]}, {"success", ok ? "b1" : "b0"},
                {"panel_visible", ok ? "b1" : "b0"}, {"context", encode(ok ? preceding_ : L"")},
                {"word_completions", "b1"}, {"handwriting_correction", "b1"},
                {"caret", "a" + std::to_string(caret.left) + "," + std::to_string(caret.top) + ","
                              + std::to_string(caret.right) + "," + std::to_string(caret.bottom)}});
        } else if (type == "stroke_end" && fields["stroke_session"] == stroke_) {
            clearStroke();
        } else if (type == "stroke_commit" && !stroke_.empty() && fields["stroke_session"] == stroke_) {
            commitStroke(fields);
        } else if (type == "stroke_delete" && !stroke_.empty() && fields["stroke_session"] == stroke_) {
            deleteBefore(fields["to_start"] == "b1", fields["request_id"]);
        } else if (type.rfind("stroke_correction_", 0) == 0 &&
                   !stroke_.empty() && fields["stroke_session"] == stroke_ && strokeEditorHasFocus()) {
            if (type == "stroke_correction_configure") {
                resetCorrection();
                correctionEnabled_ = fields["enabled"] == "b1";
                publishCorrection();
            } else if (type == "stroke_correction_request") {
                requestCorrection(fields["request_id"]);
            } else if (type == "stroke_correction_result") {
                fields.try_emplace("error");
                fields.try_emplace("original_text");
                fields.try_emplace("request_id");
                applyCorrection(fields);
            } else if (type == "stroke_correction_undo" && correctionUndo_ && !correctionBusy() && !backspace_) {
                queueCorrectionWrite(*correctionUndo_, true, L"已撤销修正");
            }
        } else if (!utterance_.empty() && fields["utterance_id"] == utterance_) {
            if (type == "update" && (phase_ == "listening" || phase_ == "finishing")) {
                raw_ = decode(fields["text"]);
                const auto error = decode(fields["error"]);
                const bool final = fields["final"] == "b1";
                if (!error.empty() || FAILED(replace(raw_, final))) {
                    replace(L"", true, true);
                    settle("error", error.empty() ? L"文本框未接受语音文字" : error);
                } else if (final) {
                    voiceRedo_.clear();
                    if (voiceStart_ >= 0 && !raw_.empty() && raw_.size() <= 65536) {
                        if (voiceUndo_.size() == 32) voiceUndo_.erase(voiceUndo_.begin());
                        voiceUndo_.push_back({voiceStart_, original_, raw_, utterance_});
                    } else {
                        voiceUndo_.clear();
                    }
                    settle("dictated");
                } else {
                    state();
                }
            } else if ((type == "finish" || type == "finish_dictation") && phase_ == "listening") {
                phase_ = "finishing";
                state();
                reply("finish_audio");
            } else if (type == "cancel" && voiceFinished()) {
                if (!voiceUndo_.empty()) requestVoiceHistory(false);
                else settle("error", L"当前输入框没有可撤销的听写");
            } else if (type == "redo" && voiceFinished()) {
                requestVoiceHistory(true);
            } else if (type == "cancel" && (phase_ == "listening" || phase_ == "finishing")) {
                if (FAILED(replace(L"", true, true))) settle("error", L"输入框未接受取消请求");
                else { raw_.clear(); ++revision_; settle("undone"); }
            } else if (type == "reset" || type == "end_input") {
                retire();
                state();
            }
        }
    }
    void tick() {
        refreshContext();
        if (!context_ || !foreground()) return;
        if (!pipe_.connected()) {
            if (!epoch_.empty()) { retire(); epoch_.clear(); }
            if (GetTickCount64() >= reconnectAt_ && probe()) {
                reconnectAt_ = GetTickCount64() + 250;
                pipe_.open();
            }
            return;
        }
        pollBackspace();
        pollCorrection();
        // Probe and publish only after the editor has released its write lock.
        if (voiceHistoryResult_) {
            auto result = std::move(*voiceHistoryResult_);
            voiceHistoryResult_.reset();
            voiceHistoryPending_ = false;
            settle(result.phase, result.error, result.source);
        } else if (voiceHistoryPending_ && GetTickCount64() >= voiceHistoryDeadline_) {
            voiceHistoryPending_ = false;
            ++revision_;
            settle("error", L"输入框未及时响应撤销或重做");
        }
        if (std::exchange(correctionCaretChanged_, false) && strokeEditorHasFocus()) state();
        std::string type;
        Fields fields;
        for (int count = 0; count < 12 && pipe_.receive(type, fields); ++count) {
            if (focusDirty_ || !foreground()) { refreshContext(); break; }
            dispatch(type, std::move(fields));
        }
    }
    static LRESULT CALLBACK windowProc(HWND window, UINT message, WPARAM w, LPARAM l) {
        if (message == WM_NCCREATE) {
            auto* create = reinterpret_cast<CREATESTRUCTW*>(l);
            SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(create->lpCreateParams));
        } else if (message == WM_TIMER) {
            auto* self = reinterpret_cast<Service*>(GetWindowLongPtrW(window, GWLP_USERDATA));
            if (self) {
                self->AddRef();
                try { self->tick(); } catch (...) { self->pipe_.close(); }
                self->Release();
            }
            return 0;
        }
        return DefWindowProcW(window, message, w, l);
    }
public:
    Service() { ++objects; }
    ~Service() { --objects; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid == IID_IUnknown || iid == IID_ITfTextInputProcessor) *out = static_cast<ITfTextInputProcessor*>(this);
        else if (iid == IID_ITfThreadMgrEventSink) *out = static_cast<ITfThreadMgrEventSink*>(this);
        else if (iid == IID_ITfThreadFocusSink) *out = static_cast<ITfThreadFocusSink*>(this);
        else if (iid == IID_ITfTextEditSink) *out = static_cast<ITfTextEditSink*>(this);
        else if (iid == IID_ITfCompositionSink) *out = static_cast<ITfCompositionSink*>(this);
        else return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override { const auto refs = --refs_; if (!refs) delete this; return refs; }
    HRESULT STDMETHODCALLTYPE Activate(ITfThreadMgr* manager, TfClientId client) override {
        manager_.reset(manager);
        client_ = client;
        BOOL focused{};
        manager->IsThreadFocus(&focused);
        threadFocus_ = focused != FALSE;
        enabled_ = true;
        auto hr = advise(manager, IID_ITfThreadMgrEventSink, static_cast<ITfThreadMgrEventSink*>(this), managerSink_);
        if (SUCCEEDED(hr)) hr = advise(manager, IID_ITfThreadFocusSink, static_cast<ITfThreadFocusSink*>(this), focusSink_);
        WNDCLASSW cls{};
        cls.hInstance = module;
        cls.lpfnWndProc = windowProc;
        windowClass_ = L"Mythlink.TSF." + std::to_wstring(reinterpret_cast<UINT_PTR>(this));
        cls.lpszClassName = windowClass_.c_str();
        if (RegisterClassW(&cls))
            window_ = CreateWindowExW(0, cls.lpszClassName, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, module, this);
        if (FAILED(hr) || !window_ || !SetTimer(window_, 1, 25, nullptr)) {
            Deactivate();
            return FAILED(hr) ? hr : E_FAIL;
        }
        if (SUCCEEDED(manager->QueryInterface(IID_ITfLangBarItemMgr, reinterpret_cast<void**>(langBar_.out())))) {
            *indicator_.out() = new (std::nothrow) InputIndicator();
            if (indicator_ && FAILED(langBar_->AddItem(indicator_.get()))) indicator_.reset();
        }
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Deactivate() override {
        enabled_ = false;
        if (indicator_ && langBar_) langBar_->RemoveItem(indicator_.get());
        indicator_.reset();
        langBar_.reset();
        if (window_) DestroyWindow(std::exchange(window_, nullptr));
        if (!windowClass_.empty()) {
            UnregisterClassW(windowClass_.c_str(), module);
            windowClass_.clear();
        }
        retire();
        pipe_.close();
        epoch_.clear();
        unadvise(context_.get(), editSink_);
        unadvise(manager_.get(), managerSink_);
        unadvise(manager_.get(), focusSink_);
        context_.reset();
        manager_.reset();
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE OnInitDocumentMgr(ITfDocumentMgr*) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE OnUninitDocumentMgr(ITfDocumentMgr*) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE OnSetFocus(ITfDocumentMgr*, ITfDocumentMgr*) override { focusDirty_ = true; return S_OK; }
    HRESULT STDMETHODCALLTYPE OnPushContext(ITfContext*) override { focusDirty_ = true; return S_OK; }
    HRESULT STDMETHODCALLTYPE OnPopContext(ITfContext*) override { focusDirty_ = true; return S_OK; }
    HRESULT STDMETHODCALLTYPE OnSetThreadFocus() override { threadFocus_ = true; return S_OK; }
    HRESULT STDMETHODCALLTYPE OnKillThreadFocus() override { threadFocus_ = false; focusDirty_ = true; return S_OK; }
    HRESULT STDMETHODCALLTYPE OnEndEdit(ITfContext* context, TfEditCookie cookie, ITfEditRecord* record) override {
        // This notification arrives AFTER DoEditSession returns. In particular,
        // writing_ has already been restored for an asynchronous ring commit.
        // TSF retains the writer identity through OnEndEdit; use it rather than
        // mistaking our own caret advance for an external editor takeover.
        BOOL ownWrite{};
        if (SUCCEEDED(context->InWriteSession(client_, &ownWrite)) && ownWrite) return S_OK;
        BOOL changed{};
        if (!writing_ && SUCCEEDED(record->GetSelectionStatus(&changed)) && changed) {
            const auto echoUntil = std::exchange(deleteEchoUntil_, 0);
            const auto now = GetTickCount64();
            if (!focusDirty_ && context == context_.get() && !stroke_.empty() &&
                    (now < echoUntil || (backspace_ && now < backspace_->deadline))) {
                EditorSelection after;
                const bool readable = SUCCEEDED(editorSelection(cookie, after));
                if (readable) {
                    if (confirmBackspace(after)) return S_OK;
                    if (now < echoUntil && after.start == deleteCaret_ && after.length == 0) return S_OK;
                }
                if (backspace_ && now < backspace_->deadline) {
                    // Some editors publish the new selection before the new
                    // text length. An unchanged range can also be a notice
                    // from a text store that exposes no surrounding text.
                    if (readable && after.start >= 0 && backspace_->before.start >= 0 &&
                            (after.start != backspace_->before.start || after.length != backspace_->before.length))
                        backspace_->selectionChanged = true;
                    return S_OK;
                }
            }
            ++selection_;
            if (!utterance_.empty() || !stroke_.empty()) focusDirty_ = true;
        }
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE OnCompositionTerminated(TfEditCookie, ITfComposition* composition) override {
        if (composition_.get() == composition) {
            composition_.reset();
            if (!writing_) focusDirty_ = true;
        }
        return S_OK;
    }
};

class Factory final : public IClassFactory {
    std::atomic<ULONG> refs_{1};
public:
    Factory() { ++objects; }
    ~Factory() { --objects; }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_IClassFactory) return E_NOINTERFACE;
        *out = static_cast<IClassFactory*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override { const auto refs = --refs_; if (!refs) delete this; return refs; }
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown* outer, REFIID iid, void** out) override {
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* service = new (std::nothrow) Service;
        if (!service) return E_OUTOFMEMORY;
        const auto hr = service->QueryInterface(iid, out);
        service->Release();
        return hr;
    }
    HRESULT STDMETHODCALLTYPE LockServer(BOOL lock) override { if (lock) ++objects; else --objects; return S_OK; }
};

std::wstring registryPath() {
    wchar_t id[40]{};
    StringFromGUID2(serviceId, id, 40);
    return std::wstring(L"Software\\Classes\\CLSID\\") + id;
}
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) module = instance;
    return TRUE;
}

STDAPI DllCanUnloadNow() { return objects == 0 ? S_OK : S_FALSE; }

// A user-initiated input gesture selects our registered profile. The host
// still waits for a fresh TSF client from the original target before writing.
STDAPI MythlinkSelectProfile(DWORD expectedPid, DWORD timeoutMs) {
    HWND window = GetForegroundWindow();
    DWORD pid = 0;
    DWORD thread = GetWindowThreadProcessId(window, &pid);
    if (!thread || !expectedPid || pid != expectedPid) return E_ABORT;
    const HRESULT com = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(com) && com != RPC_E_CHANGED_MODE) return com;
    HRESULT hr;
    {
        ComPtr<ITfInputProcessorProfileMgr> profiles;
        hr = CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER,
                              IID_ITfInputProcessorProfileMgr, reinterpret_cast<void**>(profiles.out()));
        const auto selectProfile = [&]() -> HRESULT {
            if (GetForegroundWindow() != window) return E_ABORT;
            return profiles->ActivateProfile(TF_PROFILETYPE_INPUTPROCESSOR, language,
                serviceId, profileId, nullptr, TF_IPPMF_FORSESSION | TF_IPPMF_DONTCARECURRENTINPUTLANGUAGE);
        };
        if (SUCCEEDED(hr)) hr = selectProfile();
        if (hr == S_OK && LOWORD(reinterpret_cast<ULONG_PTR>(GetKeyboardLayout(thread))) != language) {
            // TSF defers a cross-language selection until the foreground app
            // accepts the corresponding input language change.
            HKL layout = nullptr;
            HKL loaded[256];
            int count = GetKeyboardLayoutList(static_cast<int>(std::size(loaded)), loaded);
            for (int i = 0; i < count; ++i) {
                if (LOWORD(reinterpret_cast<ULONG_PTR>(loaded[i])) == language) {
                    layout = loaded[i];
                    break;
                }
            }
            if (!layout) layout = LoadKeyboardLayoutW(L"00000804", KLF_NOTELLSHELL);
            DWORD_PTR result = 0;
            if (!layout || GetForegroundWindow() != window) hr = E_ABORT;
            else if (!SendMessageTimeoutW(window, WM_INPUTLANGCHANGEREQUEST, 0,
                         reinterpret_cast<LPARAM>(layout), SMTO_ABORTIFHUNG | SMTO_BLOCK,
                         timeoutMs, &result)) hr = HRESULT_FROM_WIN32(ERROR_TIMEOUT);
            // The editor may have activated its own last-used Chinese keyboard
            // while processing the language change. Select the exact TIP again.
            else hr = selectProfile();
        }
    }
    if (SUCCEEDED(com)) CoUninitialize();
    return hr;
}

STDAPI DllGetClassObject(REFCLSID clsid, REFIID iid, void** out) {
    if (clsid != serviceId) return CLASS_E_CLASSNOTAVAILABLE;
    auto* factory = new (std::nothrow) Factory;
    if (!factory) return E_OUTOFMEMORY;
    const auto hr = factory->QueryInterface(iid, out);
    factory->Release();
    return hr;
}

STDAPI DllUnregisterServer() {
    const auto com = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    {
        ComPtr<ITfInputProcessorProfileMgr> profiles;
        ComPtr<ITfCategoryMgr> categories;
        if (SUCCEEDED(CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER,
                                       IID_ITfInputProcessorProfileMgr, reinterpret_cast<void**>(profiles.out()))))
            profiles->UnregisterProfile(serviceId, language, profileId, 0);
        if (SUCCEEDED(CoCreateInstance(CLSID_TF_CategoryMgr, nullptr, CLSCTX_INPROC_SERVER,
                                       IID_ITfCategoryMgr, reinterpret_cast<void**>(categories.out()))))
            for (const auto& category : profileCategories)
                categories->UnregisterCategory(serviceId, category, serviceId);
    }
    if (SUCCEEDED(com)) CoUninitialize();
    const auto result = RegDeleteTreeW(HKEY_LOCAL_MACHINE, registryPath().c_str());
    return result == ERROR_FILE_NOT_FOUND ? S_OK : HRESULT_FROM_WIN32(result);
}

STDAPI DllRegisterServer() {
    wchar_t path[MAX_PATH]{};
    const auto length = GetModuleFileNameW(module, path, MAX_PATH);
    if (!length || length >= MAX_PATH) return E_FAIL;
    HKEY key{};
    auto result = RegCreateKeyExW(HKEY_LOCAL_MACHINE, (registryPath() + L"\\InprocServer32").c_str(), 0,
                                nullptr, 0, KEY_SET_VALUE, nullptr, &key, nullptr);
    if (result != ERROR_SUCCESS) return HRESULT_FROM_WIN32(result);
    result = RegSetValueExW(key, nullptr, 0, REG_SZ, reinterpret_cast<const BYTE*>(path), (length + 1) * sizeof(wchar_t));
    constexpr wchar_t threading[] = L"Apartment";
    if (result == ERROR_SUCCESS)
        result = RegSetValueExW(key, L"ThreadingModel", 0, REG_SZ, reinterpret_cast<const BYTE*>(threading), sizeof(threading));
    RegCloseKey(key);
    const auto com = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    HRESULT hr = result == ERROR_SUCCESS ? (com == RPC_E_CHANGED_MODE ? S_OK : com) : HRESULT_FROM_WIN32(result);
    {
        ComPtr<ITfInputProcessorProfileMgr> profiles;
        ComPtr<ITfCategoryMgr> categories;
        if (SUCCEEDED(hr)) hr = CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER,
                                                IID_ITfInputProcessorProfileMgr, reinterpret_cast<void**>(profiles.out()));
        if (SUCCEEDED(hr)) hr = profiles->RegisterProfile(serviceId, language, profileId, description,
            static_cast<ULONG>(std::size(description) - 1), path, length,
            static_cast<ULONG>(-IDI_MYTHLINK), nullptr, 0, TRUE, 0);
        if (SUCCEEDED(hr)) hr = CoCreateInstance(CLSID_TF_CategoryMgr, nullptr, CLSCTX_INPROC_SERVER,
                                                IID_ITfCategoryMgr, reinterpret_cast<void**>(categories.out()));
        for (const auto& category : profileCategories) {
            if (FAILED(hr)) break;
            hr = categories->RegisterCategory(serviceId, category, serviceId);
        }
    }
    if (FAILED(hr)) DllUnregisterServer();
    if (SUCCEEDED(com)) CoUninitialize();
    return hr;
}
