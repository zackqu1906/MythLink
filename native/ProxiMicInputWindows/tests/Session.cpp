// Compile with the same Windows SDK/compiler as the DLL; no registration or
// target application is needed. Exercise the real OnEndEdit implementation.
#define MYTHLINK_TSF_TEST
#include "../TextService.cpp"
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
    HRESULT STDMETHODCALLTYPE GetText(TfEditCookie, DWORD, WCHAR* buffer, ULONG size, ULONG* count) override {
        *count = text.copy(buffer, (std::min<ULONG>)(size, end - start), start); return S_OK;
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
        assert(flags == TF_ST_CORRECTION);
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

struct Context final : ITfContext, ITfInsertAtSelection {
    bool ownWrite = true;
    HRESULT writerStatus = S_OK;
    TfClientId requestedClient = 0;
    ITfEditSession* pending = nullptr;
    DWORD requestedFlags = 0;
    std::wstring text;
    LONG start = 0, end = 0;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (iid != IID_ITfInsertAtSelection) return E_NOINTERFACE;
        *out = static_cast<ITfInsertAtSelection*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return 1; }
    ULONG STDMETHODCALLTYPE Release() override { return 1; }
    HRESULT STDMETHODCALLTYPE RequestEditSession(TfClientId, ITfEditSession* edit, DWORD flags, HRESULT* result) override {
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
    HRESULT STDMETHODCALLTYPE InsertTextAtSelection(TfEditCookie, DWORD, const WCHAR* value, LONG size,
                                                    ITfRange** out) override {
        text.replace(start, end - start, value, size);
        end = start + size;
        *out = new Range(text, start, end);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE InsertEmbeddedAtSelection(TfEditCookie, DWORD, IDataObject*, ITfRange**) override {
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
    static void run() {
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
        service.context_.reset();
        service.stroke_.clear();
        std::cout << "TSF session, deletion, and correction regressions passed\n";
    }
};
}

int main() { ServiceTest::run(); }
