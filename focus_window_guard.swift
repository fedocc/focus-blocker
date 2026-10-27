// Inspect Arc's native preview controls and document URLs, never page contents.
import AppKit
import ApplicationServices
import Foundation
import Darwin

func emit(_ value: [String: Any], status: Int32 = 0) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    if let option = CommandLine.arguments.firstIndex(of: "--result-socket"), option + 1 < CommandLine.arguments.count {
        let path = CommandLine.arguments[option + 1]
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { exit(10) }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8) + [0]
        guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else { exit(11) }
        withUnsafeMutablePointer(to: &address.sun_path) { pointer in
            pointer.withMemoryRebound(to: UInt8.self, capacity: bytes.count) { buffer in
                _ = bytes.withUnsafeBufferPointer { source in
                    memcpy(buffer, source.baseAddress!, bytes.count)
                }
            }
        }
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { socketAddress in
                connect(fd, socketAddress, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else { exit(12) }
        // Keep this signed process alive while the root observer verifies its
        // kernel-provided peer PID and executable path. No user-writable health file.
        var accepted: UInt8 = 0
        guard Darwin.read(fd, &accepted, 1) == 1 && accepted == 49 else { exit(13) }
        var message = data
        message.append(10)
        let sent = message.withUnsafeBytes { buffer in
            Darwin.write(fd, buffer.baseAddress!, buffer.count)
        }
        close(fd)
        guard sent == message.count else { exit(14) }
    } else {
        print(String(data: data, encoding: .utf8)!)
    }
    exit(status)
}
func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value
}
enum PreviewLocation {
    case empty
    case page(String)
    case unreadable
}

func webAddress(_ element: AXUIElement, _ name: String) -> String? {
    guard let value = attribute(element, name) else { return nil }
    if let url = value as? URL { return url.absoluteString }
    if let text = value as? String, URL(string: text)?.scheme != nil { return text }
    return nil
}

func previewLocation(_ window: AXUIElement) -> PreviewLocation {
    if let document = webAddress(window, "AXDocument") { return .page(document) }
    // Inspect accessibility roles and the document URL only. Stop at the web
    // area: do not enumerate page elements or read their text or values.
    var queue: [(AXUIElement, Int)] = [(window, 0)]
    var index = 0
    while index < queue.count && index < 500 {
        let (element, depth) = queue[index]
        index += 1
        if (attribute(element, kAXRoleAttribute) as? String) == "AXWebArea" {
            if let url = webAddress(element, "AXURL") { return .page(url) }
            return .unreadable
        }
        if depth < 12, let children = attribute(element, kAXChildrenAttribute) as? [AXUIElement] {
            queue.append(contentsOf: children.map { ($0, depth + 1) })
        }
    }
    // A new Little Arc command window has no document yet. Leave it open.
    return index >= 500 ? .unreadable : .empty
}

func blockedPreviewURL(_ value: String) -> Bool {
    guard let parsed = URLComponents(string: value), let rawHost = parsed.host else { return false }
    let host = rawHost.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
    let blocked = ["youtube.com", "youtu.be", "youtube-nocookie.com", "googlevideo.com", "ytimg.com",
                   "instagram.com", "cdninstagram.com", "ig.me", "x.com", "twitter.com", "twimg.com",
                   "web.telegram.org", "t.me", "telegram.me", "tiktok.com", "tiktokv.com", "tiktokcdn.com",
                   "musical.ly", "vkvideo.ru", "video.vk.com"]
    if blocked.contains(where: { host == $0 || host.hasSuffix("." + $0) }) { return true }
    let isGoogle = host.range(of: #"^(?:[a-z0-9-]+\.)*google\.(?:[a-z]{2,3}|(?:co|com)\.[a-z]{2})$"#, options: .regularExpression) != nil
    guard isGoogle else { return false }
    if ["/videohp", "/video", "/videos"].contains(parsed.path.trimmingCharacters(in: CharacterSet(charactersIn: "/" )).isEmpty ? "/" : "/" + parsed.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))) { return true }
    var query = parsed.queryItems ?? []
    if let fragment = parsed.fragment,
       let items = URLComponents(string: "https://preview.invalid/?" + fragment)?.queryItems { query += items }
    return query.contains { item in
        (item.name == "vld") ||
        (item.name == "fpstate" && ["ive", "v"].contains(item.value ?? "")) ||
        (["tbm", "udm"].contains(item.name) && ["vid", "vids", "7"].contains(item.value ?? ""))
    }
}

func mustClose(_ window: AXUIElement) -> Bool {
    switch previewLocation(window) {
    case .empty: return false
    case .page(let url): return blockedPreviewURL(url)
    case .unreadable: return true
    }
}

// Peek is a native overlay inside a regular Arc window. Its controls and
// document are siblings; inspecting the whole window would read the underlying
// tab's address and could close an unrelated ordinary preview.
func peekTargets(_ window: AXUIElement) -> [(AXUIElement, AXUIElement?)] {
    guard let children = attribute(window, kAXChildrenAttribute) as? [AXUIElement] else { return [] }
    var result: [(AXUIElement, AXUIElement?)] = []
    for (index, group) in children.enumerated() {
        // Never descend into a web area: sites cannot supply native controls.
        guard (attribute(group, kAXRoleAttribute) as? String) == "AXGroup",
              let controls = attribute(group, kAXChildrenAttribute) as? [AXUIElement] else { continue }
        let buttons = controls.filter { (attribute($0, kAXRoleAttribute) as? String) == "AXButton" }
        guard let close = buttons.first(where: { (attribute($0, "AXIdentifier") as? String) == "confirmCloseModal" }),
              buttons.contains(where: { (attribute($0, "AXIdentifier") as? String) == "openInSpaceExpander" }) else { continue }
        let next = index + 1 < children.count ? children[index + 1] : nil
        let content = next.flatMap { (attribute($0, kAXRoleAttribute) as? String) == "AXSplitGroup" ? $0 : nil }
        result.append((close, content))
    }
    return result
}

func forbiddenPeek(_ content: AXUIElement?) -> Bool {
    guard let content = content else { return true }
    return mustClose(content)
}

if CommandLine.arguments.contains("--self-test") {
    let denied = ["https://youtube.com/watch?v=abcdefghijk", "https://www.youtube.com/", "https://youtu.be/abcdefghijk",
                  "https://youtube-nocookie.com/embed/abcdefghijk", "https://m.instagram.com/", "https://x.com/",
                  "https://web.telegram.org/k/", "https://vm.tiktok.com/a", "https://vkvideo.ru/video1",
                  "https://www.google.com/search?q=test#fpstate=ive&vld=vid:abcdefghijk", "https://www.google.ru/search?udm=7", "https://www.google.com/videohp"]
    let allowed = ["https://example.com/", "https://en.wikipedia.org/wiki/Learning", "https://www.google.com/search?q=lecture",
                   "https://docs.google.com/document/d/example", "https://github.com/", "about:blank",
                   "https://example.com/youtube.com", "https://youtube.com.example.org/", "https://example.com/?q=x.com", "https://google.com.example.org/search?udm=7"]
    for url in denied { precondition(blockedPreviewURL(url), "Forbidden preview was allowed: " + url) }
    for url in allowed { precondition(!blockedPreviewURL(url), "Ordinary preview was denied: " + url) }
    emit(["ok": true, "url_cases": denied.count + allowed.count])
}

let request = CommandLine.arguments.contains("--request-permission")
let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: request] as CFDictionary
let trusted = AXIsProcessTrustedWithOptions(options)
if request { emit(["trusted": trusted]) }
guard trusted else { emit(["ok": false, "error": "Focus Link Guard needs Accessibility permission"], status: 2) }
var closed = 0
var allowed = 0
for app in NSRunningApplication.runningApplications(withBundleIdentifier: "company.thebrowser.Browser") {
    let process = AXUIElementCreateApplication(app.processIdentifier)
    guard let windows = attribute(process, kAXWindowsAttribute) as? [AXUIElement] else {
        if app.isTerminated { continue }
        emit(["ok": false, "error": "Cannot observe Arc native windows"], status: 3)
    }
    for window in windows {
        guard let identifier = attribute(window, "AXIdentifier") as? String else { continue }
        if identifier.hasPrefix("bigBrowserWindow-") {
            for (close, content) in peekTargets(window) {
                if !forbiddenPeek(content) { allowed += 1; continue }
                let action = AXUIElementPerformAction(close, kAXPressAction as CFString)
                guard action == .success || action == .invalidUIElement else {
                    emit(["ok": false, "error": "Arc Peek close action failed", "code": action.rawValue], status: 8)
                }
                closed += 1
            }
            continue
        }
        guard identifier.hasPrefix("littleBrowserWindow-") else { continue }
        if !mustClose(window) { allowed += 1; continue }
        guard let control = attribute(window, kAXCloseButtonAttribute),
              CFGetTypeID(control) == AXUIElementGetTypeID() else {
            emit(["ok": false, "error": "Little Arc has no native close control"], status: 4)
        }
        let close = unsafeBitCast(control, to: AXUIElement.self)
        let result = AXUIElementPerformAction(close, kAXPressAction as CFString)
        guard result == .success || result == .invalidUIElement else {
            emit(["ok": false, "error": "Little Arc close action failed", "code": result.rawValue], status: 5)
        }
        closed += 1
    }
    guard let remaining = attribute(process, kAXWindowsAttribute) as? [AXUIElement] else {
        if app.isTerminated { continue }
        emit(["ok": false, "error": "Cannot verify Arc windows"], status: 6)
    }
    for window in remaining {
        if let identifier = attribute(window, "AXIdentifier") as? String,
           identifier.hasPrefix("bigBrowserWindow-"),
           peekTargets(window).contains(where: { forbiddenPeek($0.1) }) {
            emit(["ok": false, "error": "Forbidden Arc Peek is still open"], status: 9)
        }
        if let identifier = attribute(window, "AXIdentifier") as? String,
           identifier.hasPrefix("littleBrowserWindow-"), mustClose(window) {
            emit(["ok": false, "error": "Forbidden Little Arc preview is still open"], status: 7)
        }
    }
}
emit(["ok": true, "closed": closed, "allowed": allowed])
