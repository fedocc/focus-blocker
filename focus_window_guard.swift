// Inspect only Arc's native window identity. Never read web page contents.
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
let request = CommandLine.arguments.contains("--request-permission")
let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: request] as CFDictionary
let trusted = AXIsProcessTrustedWithOptions(options)
if request { emit(["trusted": trusted]) }
guard trusted else { emit(["ok": false, "error": "Focus Window Guard needs Accessibility permission"], status: 2) }
var closed = 0
for app in NSRunningApplication.runningApplications(withBundleIdentifier: "company.thebrowser.Browser") {
    let process = AXUIElementCreateApplication(app.processIdentifier)
    guard let windows = attribute(process, kAXWindowsAttribute) as? [AXUIElement] else {
        if app.isTerminated { continue }
        emit(["ok": false, "error": "Cannot observe Arc native windows"], status: 3)
    }
    for window in windows {
        guard let identifier = attribute(window, "AXIdentifier") as? String,
              identifier.hasPrefix("littleBrowserWindow-") else { continue }
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
           identifier.hasPrefix("littleBrowserWindow-") {
            emit(["ok": false, "error": "Little Arc preview is still open"], status: 7)
        }
    }
}
emit(["ok": true, "closed": closed])
