import AppKit
import Foundation
import PDFKit
import Vision

enum ExtractionError: Error, CustomStringConvertible {
    case cannotOpen(String)
    case cannotRender(String)

    var description: String {
        switch self {
        case .cannotOpen(let path):
            return "Could not open \(path)"
        case .cannotRender(let path):
            return "Could not render \(path)"
        }
    }
}

func cgImage(from image: NSImage) -> CGImage? {
    var bounds = NSRect(origin: .zero, size: image.size)
    return image.cgImage(
        forProposedRect: &bounds,
        context: nil,
        hints: nil
    )
}

func recognise(_ image: CGImage) throws -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.automaticallyDetectsLanguage = true

    let handler = VNImageRequestHandler(
        cgImage: image,
        orientation: .up,
        options: [:]
    )
    try handler.perform([request])
    let observations = (request.results ?? []).sorted { left, right in
        let verticalDifference = left.boundingBox.minY - right.boundingBox.minY
        if abs(verticalDifference) > 0.012 {
            return left.boundingBox.minY > right.boundingBox.minY
        }
        return left.boundingBox.minX < right.boundingBox.minX
    }
    return observations.compactMap {
        $0.topCandidates(1).first?.string
    }.joined(separator: "\n")
}

func extractImage(_ url: URL) throws -> String {
    guard let image = NSImage(contentsOf: url) else {
        throw ExtractionError.cannotOpen(url.path)
    }
    guard let rendered = cgImage(from: image) else {
        throw ExtractionError.cannotRender(url.path)
    }
    return try recognise(rendered)
}

func extractPDF(_ url: URL) throws -> String {
    guard let document = PDFDocument(url: url) else {
        throw ExtractionError.cannotOpen(url.path)
    }
    var pages: [String] = []
    for index in 0..<document.pageCount {
        guard let page = document.page(at: index) else {
            continue
        }
        let nativeText = (page.string ?? "").trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        if nativeText.count >= 12 {
            pages.append(nativeText)
            continue
        }

        let image = page.thumbnail(
            of: NSSize(width: 2200, height: 2200),
            for: .mediaBox
        )
        guard let rendered = cgImage(from: image) else {
            continue
        }
        let recognised = try recognise(rendered).trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        if !recognised.isEmpty {
            pages.append(recognised)
        }
    }
    return pages.joined(separator: "\n\n")
}

guard CommandLine.arguments.count == 2 else {
    fputs("Usage: vision-extract <image-or-pdf>\n", stderr)
    exit(2)
}

let url = URL(fileURLWithPath: CommandLine.arguments[1])
do {
    let text: String
    if url.pathExtension.lowercased() == "pdf" {
        text = try extractPDF(url)
    } else {
        text = try extractImage(url)
    }
    FileHandle.standardOutput.write(Data(text.utf8))
} catch {
    fputs("\(error)\n", stderr)
    exit(1)
}
