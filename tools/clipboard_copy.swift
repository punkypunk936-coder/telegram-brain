import AppKit
import Foundation
import UniformTypeIdentifiers

enum ClipboardCopyError: Error, CustomStringConvertible {
    case invalidArguments
    case unreadableImage(String)
    case writeFailed

    var description: String {
        switch self {
        case .invalidArguments:
            return "Usage: clipboard-copy <text|image|files> [paths...]"
        case .unreadableImage(let path):
            return "Could not read image: \(path)"
        case .writeFailed:
            return "The macOS clipboard rejected the item."
        }
    }
}

func copyText() throws {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard let text = String(data: data, encoding: .utf8) else {
        throw ClipboardCopyError.invalidArguments
    }
    let pasteboard = NSPasteboard.general
    pasteboard.clearContents()
    guard pasteboard.setString(text, forType: .string) else {
        throw ClipboardCopyError.writeFailed
    }
}

func copyImage(path: String) throws {
    guard let image = NSImage(contentsOfFile: path) else {
        throw ClipboardCopyError.unreadableImage(path)
    }
    let pasteboard = NSPasteboard.general
    pasteboard.clearContents()
    guard pasteboard.writeObjects([image]) else {
        throw ClipboardCopyError.writeFailed
    }
}

func copyFiles(paths: [String]) throws {
    let pasteboard = NSPasteboard.general
    pasteboard.clearContents()

    if paths.count == 1 {
        let url = URL(fileURLWithPath: paths[0])
        let item = NSPasteboardItem()
        guard item.setString(url.absoluteString, forType: .fileURL) else {
            throw ClipboardCopyError.writeFailed
        }
        if url.pathExtension.lowercased() == "gif",
           let data = try? Data(contentsOf: url) {
            guard item.setData(
                data,
                forType: NSPasteboard.PasteboardType(UTType.gif.identifier)
            ) else {
                throw ClipboardCopyError.writeFailed
            }
        }
        guard pasteboard.writeObjects([item]) else {
            throw ClipboardCopyError.writeFailed
        }
        return
    }

    let urls = paths.map { NSURL(fileURLWithPath: $0) }
    guard pasteboard.writeObjects(urls) else {
        throw ClipboardCopyError.writeFailed
    }
}

do {
    guard CommandLine.arguments.count >= 2 else {
        throw ClipboardCopyError.invalidArguments
    }
    let mode = CommandLine.arguments[1]
    let paths = Array(CommandLine.arguments.dropFirst(2))
    switch mode {
    case "text":
        try copyText()
    case "image":
        guard paths.count == 1 else {
            throw ClipboardCopyError.invalidArguments
        }
        try copyImage(path: paths[0])
    case "files":
        guard !paths.isEmpty else {
            throw ClipboardCopyError.invalidArguments
        }
        try copyFiles(paths: paths)
    default:
        throw ClipboardCopyError.invalidArguments
    }
} catch {
    fputs("\(error)\n", stderr)
    exit(1)
}
