import AppKit
guard CommandLine.arguments.count > 1 else {
    fputs("Use scripts/start-ring-agent.sh to launch this window.\n", stderr)
    exit(2)
}
let root=URL(fileURLWithPath:CommandLine.arguments[1])
final class TopDocument:NSView { override var isFlipped:Bool {true} }
final class Console:NSObject,NSApplicationDelegate,NSWindowDelegate {
    var window:NSWindow!
    var stack=NSStackView(), choices=NSStackView()
    var permissionContent=NSStackView(), questionContent=NSStackView(), requestContent=NSStackView()
    var questionSection:NSStackView!
    var requestIsQuestion=false
    var status=NSTextField(wrappingLabelWithString:"准备中")
    var detail=NSTextField(wrappingLabelWithString:"")
    var ring=NSTextField(wrappingLabelWithString:"")
    var queued=NSTextField(wrappingLabelWithString:"")
    var submission=NSTextField(wrappingLabelWithString:"")
    var requestTitle=NSTextField(wrappingLabelWithString:"")
    var requestBody=NSTextField(wrappingLabelWithString:"")
    var selection=NSTextField(wrappingLabelWithString:"")
    var raw=NSTextField(wrappingLabelWithString:"")
    var grants=NSTextField(wrappingLabelWithString:"")
    var output=NSTextField(wrappingLabelWithString:"")
    var autoApprovalStatus=NSTextField(wrappingLabelWithString:"")
    var history=NSTextField(wrappingLabelWithString:"")
    var transcript=NSTextField(wrappingLabelWithString:"")
    var agent=NSTextField(wrappingLabelWithString:"")
    var input=NSTextField()
    var answerInput=NSTextField()
    var answerControls=NSStackView()
    var answerTranscript=NSTextField(wrappingLabelWithString:"")
    var answerButton:NSButton!
    var rawButton:NSButton!, autoApproveButton:NSButton!
    var revision = -1, showRaw=false
    var requestToken="", rawText=""
    func label(_ text:String,_ size:CGFloat=13,_ weight:NSFont.Weight = .regular)->NSTextField {
        let v=NSTextField(wrappingLabelWithString:text);v.font = .systemFont(ofSize:size,weight:weight);return v
    }
    func button(_ title:String,_ action:Selector)->NSButton {
        let b=NSButton(title:title,target:self,action:action);b.bezelStyle = .rounded;return b
    }
    func add(_ view:NSView) {stack.addArrangedSubview(view);view.widthAnchor.constraint(equalTo:stack.widthAnchor).isActive=true}
    func column()->NSStackView {
        let v=NSStackView();v.orientation = .vertical;v.alignment = .leading;v.spacing=8
        v.translatesAutoresizingMaskIntoConstraints=false;return v
    }
    func put(_ view:NSView,in container:NSStackView) {
        container.addArrangedSubview(view);view.widthAnchor.constraint(equalTo:container.widthAnchor).isActive=true
    }
    func section(_ title:String,content:NSStackView,color:NSColor)->NSStackView {
        let box=column();box.edgeInsets=NSEdgeInsets(top:12,left:12,bottom:12,right:12)
        box.wantsLayer=true;box.layer?.cornerRadius=10;box.layer?.borderWidth=1
        box.layer?.borderColor=color.withAlphaComponent(0.35).cgColor
        box.layer?.backgroundColor=NSColor.controlBackgroundColor.cgColor
        let heading=label(title,14,.semibold);heading.textColor=color
        box.addArrangedSubview(heading);box.addArrangedSubview(content)
        content.widthAnchor.constraint(equalTo:box.widthAnchor,constant:-24).isActive=true
        return box
    }
    func applicationDidFinishLaunching(_ notification:Notification) {
        NSApp.setActivationPolicy(.regular)
        window=NSWindow(contentRect:NSRect(x:0,y:0,width:690,height:850),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        window.minSize=NSSize(width:600,height:680)
        window.title="戒指 Agent · 任务与请求";window.level = .floating
        window.collectionBehavior=[.canJoinAllSpaces,.fullScreenAuxiliary];window.delegate=self;window.isReleasedWhenClosed=false
        let host=window.contentView!, header=column(), footer=column()
        host.addSubview(header);host.addSubview(footer)
        NSLayoutConstraint.activate([
            header.leadingAnchor.constraint(equalTo:host.leadingAnchor,constant:18),header.trailingAnchor.constraint(equalTo:host.trailingAnchor,constant:-18),header.topAnchor.constraint(equalTo:host.topAnchor,constant:16),
            footer.leadingAnchor.constraint(equalTo:host.leadingAnchor,constant:18),footer.trailingAnchor.constraint(equalTo:host.trailingAnchor,constant:-18),footer.bottomAnchor.constraint(equalTo:host.bottomAnchor,constant:-16)
        ])
        put(label("戒指 → 桌面 Agent",21,.bold),in:header)
        status.font = .systemFont(ofSize:16,weight:.semibold);status.textColor = .systemBlue
        put(status,in:header);put(ring,in:header)
        let controls=NSStackView();controls.spacing=8
        controls.addArrangedSubview(button("取消任务和队列 · 左滑",#selector(cancel)))
        controls.addArrangedSubview(button("结束测试",#selector(exitTest)));header.addArrangedSubview(controls)

        let scroll=NSScrollView();scroll.hasVerticalScroller=true;scroll.translatesAutoresizingMaskIntoConstraints=false
        host.addSubview(scroll)
        NSLayoutConstraint.activate([scroll.leadingAnchor.constraint(equalTo:host.leadingAnchor),scroll.trailingAnchor.constraint(equalTo:host.trailingAnchor),scroll.topAnchor.constraint(equalTo:header.bottomAnchor,constant:12),scroll.bottomAnchor.constraint(equalTo:footer.topAnchor,constant:-12)])
        let document=TopDocument();document.translatesAutoresizingMaskIntoConstraints=false;scroll.documentView=document
        document.widthAnchor.constraint(equalTo:scroll.contentView.widthAnchor).isActive=true
        stack=column();stack.spacing=12;document.addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo:document.leadingAnchor,constant:18),stack.trailingAnchor.constraint(equalTo:document.trailingAnchor,constant:-18),stack.topAnchor.constraint(equalTo:document.topAnchor,constant:8),stack.bottomAnchor.constraint(equalTo:document.bottomAnchor,constant:-8)])

        permissionContent=column();questionContent=column();requestContent=column()
        autoApproveButton=NSButton(checkboxWithTitle:"自动允许授权请求（本次运行）",target:self,action:#selector(toggleAutoApprove))
        autoApproveButton.toolTip="开启期间自动回应可处理的授权请求；Agent 追问保留。关闭不撤销已生效的权限。"
        put(autoApproveButton,in:permissionContent);put(autoApprovalStatus,in:permissionContent)
        put(label("只自动处理授权，不会替你回答 Agent。登录、系统验证和需单独确认的请求仍会显示。",11),in:permissionContent)
        grants.font = .systemFont(ofSize:11);put(grants,in:permissionContent)
        requestTitle.font = .systemFont(ofSize:15,weight:.semibold)
        put(requestTitle,in:requestContent);put(requestBody,in:requestContent)
        selection.font = .systemFont(ofSize:12,weight:.medium);put(selection,in:requestContent)
        choices=column();put(choices,in:requestContent)
        rawButton=button("查看完整请求详情",#selector(toggleRaw));requestContent.addArrangedSubview(rawButton)
        raw.font = .monospacedSystemFont(ofSize:11,weight:.regular);raw.isSelectable=true;raw.isHidden=true;put(raw,in:requestContent)
        permissionContent.addArrangedSubview(requestContent)
        add(section("授权与权限提示",content:permissionContent,color:.systemOrange))

        let progress=column();put(detail,in:progress)
        agent.maximumNumberOfLines=6;put(agent,in:progress)
        transcript.maximumNumberOfLines=2;transcript.textColor = .secondaryLabelColor;put(transcript,in:progress)
        output.font = .systemFont(ofSize:11);output.textColor = .secondaryLabelColor;put(output,in:progress)
        history.font = .systemFont(ofSize:11);history.textColor = .secondaryLabelColor;put(history,in:progress)
        add(section("任务进展",content:progress,color:.secondaryLabelColor))

        answerControls=column()
        answerInput.placeholderString="在这里回答上方问题（回车提交回答）";answerInput.setAccessibilityLabel("回答 Agent")
        answerInput.target=self;answerInput.action=#selector(submitAnswer);put(answerInput,in:answerControls)
        answerButton=button("提交回答",#selector(submitAnswer));answerControls.addArrangedSubview(answerButton)
        put(label("语音回答：双击 tap 开始 → 单击 tap 结束并提交",12),in:answerControls)
        put(answerTranscript,in:answerControls);questionContent.addArrangedSubview(answerControls)
        answerControls.widthAnchor.constraint(equalTo:questionContent.widthAnchor).isActive=true
        questionSection=section("Agent 提问 · 等待你的回答",content:questionContent,color:.systemBlue)
        questionSection.isHidden=true;add(questionSection)
        // Both parents always have the same width; this survives moving the request card.
        requestContent.widthAnchor.constraint(equalTo:permissionContent.widthAnchor).isActive=true

        let separator=NSBox();separator.boxType = .separator;put(separator,in:footer)
        put(label("新任务 / 修改当前任务",14,.semibold),in:footer)
        input.placeholderString="新任务指令；执行中提交会排队";input.target=self;input.action=#selector(submit)
        input.setAccessibilityLabel("任务指令");put(input,in:footer)
        let textActions=NSStackView();textActions.spacing=8
        textActions.addArrangedSubview(button("提交任务 / 排队",#selector(submit)))
        textActions.addArrangedSubview(button("修改当前任务（steer）",#selector(submitSteer)));footer.addArrangedSubview(textActions)
        queued.maximumNumberOfLines=2;put(queued,in:footer);put(submission,in:footer)
        put(label("双击说任务；tap 提交，录音时右滑 steer。回答 Agent 请使用上方回答区。",11),in:footer)
        window.center();window.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)
        Timer.scheduledTimer(withTimeInterval:0.15,repeats:true){[weak self]_ in self?.refresh()}
    }
    func placeRequest(isQuestion:Bool) {
        questionSection.isHidden = !isQuestion
        if requestIsQuestion != isQuestion {
            (requestContent.superview as? NSStackView)?.removeArrangedSubview(requestContent)
            requestContent.removeFromSuperview()
            if isQuestion {questionContent.insertArrangedSubview(requestContent,at:0)}
            else {permissionContent.addArrangedSubview(requestContent)}
            requestIsQuestion=isQuestion
        }
        requestTitle.textColor=isQuestion ? .systemBlue:.systemOrange
    }
    func send(_ type:String,text:String="",index:Int?=nil,enabled:Bool?=nil,questionToken:String?=nil) {
        var data:[String:Any]=["type":type,"text":text]
        if let i=index {data["token"]=requestToken;data["index"]=i}
        if let value=enabled {data["enabled"]=value}
        if let token=questionToken {data["questionToken"]=token}
        let stamp=Int64(Date().timeIntervalSince1970*1_000_000)
        let path=root.appendingPathComponent("commands/\(stamp)-\(UUID().uuidString).json")
        try? FileManager.default.createDirectory(at:path.deletingLastPathComponent(),withIntermediateDirectories:true)
        if let bytes=try? JSONSerialization.data(withJSONObject:data){try? bytes.write(to:path,options:.atomic)}
    }
    func refresh() {
        guard let bytes=try? Data(contentsOf:root.appendingPathComponent("state.json")),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],let next=state["revision"] as? Int,next != revision else{return}
        revision=next;status.stringValue=state["status"] as? String ?? "";detail.stringValue=state["detail"] as? String ?? ""
        let automatic=state["autoApprove"] as? Bool ?? false
        autoApproveButton.state=automatic ? .on:.off
        autoApprovalStatus.stringValue="自动允许：\(automatic ? "已开启":"已关闭") · 本次自动处理 \(state["autoApprovedCount"] as? Int ?? 0) 个授权请求"
        if let preference=state["outputDirectory"] as? String, !preference.isEmpty {
            output.stringValue="保存位置偏好："+preference+"\n指定其他位置时，以你的要求为准。"
        } else {
            output.stringValue="保存位置：按你的要求；未指定时沿用应用当前保存位置。"
        }
        ring.stringValue=state["ring"] as? String ?? "";transcript.stringValue=state["transcript"] as? String ?? "";agent.stringValue=state["agent"] as? String ?? ""
        submission.stringValue=state["lastSubmission"] as? String ?? ""
        let pendingTasks=state["taskQueue"] as? [[String:Any]] ?? []
        queued.stringValue=pendingTasks.isEmpty ? "等待队列：空":"等待队列（\(pendingTasks.count) 条）：\n"+pendingTasks.enumerated().map{"\($0.offset+1). \($0.element["text"] as? String ?? "")"}.joined(separator:"\n")
        let apps=state["grantedApps"] as? [String] ?? []
        grants.stringValue=apps.isEmpty ? (automatic ? "新应用的普通授权将自动处理":"尚未授权应用；首次使用时会询问"):"已授权应用："+apps.joined(separator:"、")+"（普通操作自动执行）"
        history.stringValue=(state["history"] as? [String] ?? []).suffix(4).joined(separator:"\n")
        for view in choices.arrangedSubviews{choices.removeArrangedSubview(view);view.removeFromSuperview()}
        if let r=state["request"] as? [String:Any] {
            let token=r["token"] as? String ?? ""
            let newRequest=token != requestToken
            if newRequest {answerInput.stringValue="";showRaw=false;window.deminiaturize(nil);window.orderFrontRegardless();NSApp.requestUserAttention(.informationalRequest)}
            requestToken=token
            let isQuestion=(r["kind"] as? String)=="question"
            placeRequest(isQuestion:isQuestion)
            let canAnswer=isQuestion && (r["blocked"] as? String ?? "").isEmpty
            answerInput.isEnabled=canAnswer;answerButton.isEnabled=canAnswer
            answerTranscript.stringValue=state["answerTranscript"] as? String ?? ""
            let title=r["title"] as? String ?? "请求", id=r["requestId"] as? String ?? "", source=r["source"] as? String ?? "", method=r["method"] as? String ?? ""
            let count=r["optionCount"] as? Int ?? 0, queued=state["requestCount"] as? Int ?? 0
            requestTitle.stringValue="\(title) · 请求 #\(id) · 待处理 \(queued) 条"
            requestBody.stringValue="来源：\(source) / \(method)\n\(r["message"] as? String ?? "")\n\(r["blocked"] as? String ?? "")\n\(r["status"] as? String ?? "")"
            selection.stringValue="服务器提供 \(count) 个选项 · 当前选中：\(r["selection"] as? String ?? "")\n上滑 / 下滑选择 → tap 确认；双击开始语音"
            for o in r["options"] as? [[String:Any]] ?? [] {
                let i=o["index"] as? Int ?? 0, chosen=o["selected"] as? Bool ?? false
                let title="\(chosen ? "▶" : "○") \(i+1). \(o["label"] as? String ?? "")"
                let b=button(title,#selector(choose(_:)));b.tag=i;b.isEnabled=o["enabled"] as? Bool ?? false
                b.toolTip=(o["reason"] as? String ?? "")+"\n"+(o["gesture"] as? String ?? "")
                b.setAccessibilityHelp(b.toolTip);choices.addArrangedSubview(b)
                if let reason=o["reason"] as? String,!reason.isEmpty {
                    let v=label(reason,11);v.textColor = .secondaryLabelColor;choices.addArrangedSubview(v);v.widthAnchor.constraint(equalTo:choices.widthAnchor).isActive=true
                }
            }
            rawText=r["details"] as? String ?? "";rawButton.isHidden=false
            if newRequest {
                window.contentView?.layoutSubtreeIfNeeded()
                requestContent.scrollToVisible(requestContent.bounds)
                if canAnswer {window.makeFirstResponder(answerInput)}
            }
        } else {placeRequest(isQuestion:false);requestToken="";requestTitle.stringValue="当前没有待处理请求";requestBody.stringValue="";selection.stringValue="双击开始语音 · tap 普通提交 · 右滑 steer";rawText="";showRaw=false;rawButton.isHidden=true}
        raw.stringValue=rawText;raw.isHidden = !showRaw;rawButton.title=showRaw ? "收起请求详情":"查看完整请求详情"
    }
    @objc func choose(_ sender:NSButton){send("choose",index:sender.tag)}
    @objc func toggleRaw(){showRaw.toggle();raw.isHidden = !showRaw;rawButton.title=showRaw ? "收起请求详情":"查看完整请求详情"}
    @objc func submit(){let text=input.stringValue;input.stringValue="";send("text",text:text)}
    @objc func submitAnswer(){send("answer",text:answerInput.stringValue,questionToken:requestToken)}
    @objc func submitSteer(){let text=input.stringValue;input.stringValue="";send("steer",text:text)}
    @objc func toggleAutoApprove(){send("set_auto_approve",enabled:autoApproveButton.state == .on)}
    @objc func cancel(){send("cancel")}
    @objc func exitTest(){send("exit")}
    func windowShouldClose(_ sender:NSWindow)->Bool{send("exit");NSApp.terminate(nil);return true}
}
let app=NSApplication.shared;let delegate=Console();app.delegate=delegate;app.run()
