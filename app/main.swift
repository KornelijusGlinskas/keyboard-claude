import Cocoa
import SwiftUI

// MARK: - Configuration

let DAEMON_PORT = 8787
let API_BASE = "http://localhost:\(DAEMON_PORT)"
let PID_FILE = "/tmp/keyboard-claude-daemon.pid"

var projectDir: String {
    URL(fileURLWithPath: Bundle.main.bundlePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .path
}

// MARK: - Color Palette

extension Color {
    static let bg       = Color(red: 10/255, green: 10/255, blue: 10/255)
    static let surface  = Color(red: 20/255, green: 20/255, blue: 20/255)
    static let border   = Color(red: 30/255, green: 30/255, blue: 30/255)
    static let textPrimary = Color(red: 224/255, green: 224/255, blue: 224/255)
    static let textDim  = Color(red: 136/255, green: 136/255, blue: 136/255)
    static let textMuted = Color(red: 85/255, green: 85/255, blue: 85/255)
    static let kbd_orange = Color(red: 222/255, green: 115/255, blue: 86/255)
    static let kbd_green  = Color(red: 74/255, green: 222/255, blue: 128/255)
    static let kbd_red    = Color(red: 239/255, green: 68/255, blue: 68/255)
}

// MARK: - Data Models

struct SessionData: Codable, Identifiable {
    let session_id: String
    let state: String
    let slot: Int
    let led_index: Int
    let idle_seconds: Double
    let iterm_session: String

    var id: String { session_id }
}

struct EventData: Codable, Identifiable {
    let ts: Double?
    let session: String?
    let event: String?
    let tool: String?
    let notif: String?

    var id: String {
        "\(ts ?? 0)-\(session ?? "")-\(event ?? "")-\(tool ?? "")"
    }
}

struct StatusData: Codable {
    let connected: Bool
    let `protocol`: String
    let uptime_seconds: Double
    let slots_used: Int
    let slots_total: Int
}

struct SessionsResponse: Codable { let sessions: [SessionData] }
struct EventsResponse: Codable { let events: [EventData] }

// MARK: - ViewModel

@Observable
final class DashboardViewModel {
    var status = StatusData(connected: false, protocol: "", uptime_seconds: 0, slots_used: 0, slots_total: 8)
    var sessions: [SessionData] = []
    var events: [EventData] = []
    var filterText = ""
    var connectionState: ConnectionState = .connecting

    enum ConnectionState {
        case connecting
        case connected
        case error(String)
    }

    var filteredEvents: [EventData] {
        guard !filterText.isEmpty else { return events }
        let q = filterText.lowercased()
        return events.filter {
            ($0.event ?? "").lowercased().contains(q) ||
            ($0.session ?? "").lowercased().contains(q) ||
            ($0.tool ?? "").lowercased().contains(q) ||
            ($0.notif ?? "").lowercased().contains(q)
        }
    }

    private var pollTimer: Timer?
    private var pollErrors = 0

    func startPolling() {
        pollTimer?.invalidate()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.poll()
        }
        poll()
    }

    func stopPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    private func poll() {
        let group = DispatchGroup()
        var newStatus: StatusData?
        var newSessions: [SessionData]?
        var newEvents: [EventData]?
        var hadError = false

        group.enter()
        fetch(StatusData.self, path: "/api/status") { result in
            if case .success(let s) = result { newStatus = s } else { hadError = true }
            group.leave()
        }

        group.enter()
        fetch(SessionsResponse.self, path: "/api/sessions") { result in
            if case .success(let r) = result { newSessions = r.sessions } else { hadError = true }
            group.leave()
        }

        group.enter()
        fetch(EventsResponse.self, path: "/api/events?n=200") { result in
            if case .success(let r) = result { newEvents = r.events } else { hadError = true }
            group.leave()
        }

        group.notify(queue: .main) { [weak self] in
            guard let self else { return }
            if hadError {
                self.pollErrors += 1
                if self.pollErrors > 5 {
                    self.connectionState = .error("Dashboard disconnected")
                }
            } else {
                self.pollErrors = 0
                if let s = newStatus { self.status = s }
                if let s = newSessions { self.sessions = s.sorted { $0.slot < $1.slot } }
                if let e = newEvents { self.events = e }
                self.connectionState = .connected
            }
        }
    }

    private func fetch<T: Decodable>(_ type: T.Type, path: String, completion: @escaping (Result<T, Error>) -> Void) {
        guard let url = URL(string: API_BASE + path) else { return }
        URLSession.shared.dataTask(with: url) { data, response, error in
            if let error { completion(.failure(error)); return }
            guard let data, (response as? HTTPURLResponse)?.statusCode == 200 else {
                completion(.failure(URLError(.badServerResponse))); return
            }
            do {
                let decoded = try JSONDecoder().decode(type, from: data)
                completion(.success(decoded))
            } catch {
                completion(.failure(error))
            }
        }.resume()
    }

    // MARK: Stats

    var statCounts: [(String, Int)] {
        var counts: [String: Int] = [:]
        for e in events {
            let key = e.event ?? "unknown"
            counts[key, default: 0] += 1
        }
        return counts.sorted { $0.value > $1.value }.prefix(6).map { ($0.key, $0.value) }
    }

    var avgResponseTime: Double? {
        var responseTimes: [Double] = []
        var lastStop: [String: Double] = [:]
        for e in events {
            guard let session = e.session, let ts = e.ts else { continue }
            if e.event == "Stop" { lastStop[session] = ts }
            else if e.event == "UserPromptSubmit", let stopTs = lastStop[session] {
                responseTimes.append(ts - stopTs)
                lastStop.removeValue(forKey: session)
            }
        }
        guard !responseTimes.isEmpty else { return nil }
        return responseTimes.reduce(0, +) / Double(responseTimes.count)
    }
}

// MARK: - Helpers

func formatDuration(_ s: Double) -> String {
    if s < 60 { return "\(Int(s.rounded()))s" }
    if s < 3600 { return "\(Int(s) / 60)m \(Int(s) % 60)s" }
    return "\(Int(s) / 3600)h \(Int(s) % 3600 / 60)m"
}

func formatTime(_ ts: Double?) -> String {
    guard let ts else { return "" }
    let date = Date(timeIntervalSince1970: ts)
    let fmt = DateFormatter()
    fmt.dateFormat = "HH:mm:ss"
    return fmt.string(from: date)
}

func eventColor(_ event: String?, _ notif: String?) -> Color {
    guard let event else { return .textDim }
    if event == "Stop" { return .kbd_orange }
    if event == "Notification" && (notif == "permission_prompt" || notif == "elicitation_dialog") { return .kbd_orange }
    if event == "PreToolUse" || event == "UserPromptSubmit" { return .textDim }
    if event == "Notification" { return .kbd_green }
    return .textDim
}

func eventBgColor(_ event: String?, _ notif: String?) -> Color {
    guard let event else { return .clear }
    if event == "Stop" { return .kbd_orange.opacity(0.15) }
    if event == "Notification" && (notif == "permission_prompt" || notif == "elicitation_dialog") { return .kbd_orange.opacity(0.15) }
    if event == "PreToolUse" || event == "UserPromptSubmit" { return Color.white.opacity(0.06) }
    if event == "Notification" { return .kbd_green.opacity(0.15) }
    return .clear
}

// MARK: - Keyboard Layout

let LAYOUT: [[Int?]] = [
    [nil, 10, 11, nil],
    [9,   8,  7,  6],
    [2,   3,  4,  5],
    [nil,  1,  0, nil],
]

let SLOT_LEDS = [9, 8, 7, 6, 2, 3, 4, 5]
let DIM_TIMEOUT: Double = 300

// MARK: - Views

struct DashboardView: View {
    @Bindable var vm: DashboardViewModel

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                StatusBarView(status: vm.status)
                mainContent
                EventLogView(events: vm.filteredEvents, filterText: $vm.filterText)
                StatsBarView(counts: vm.statCounts, avgResponse: vm.avgResponseTime)
            }
            .padding(20)
        }
        .background(Color.bg)
    }

    var mainContent: some View {
        HStack(alignment: .top, spacing: 20) {
            VStack(alignment: .leading, spacing: 8) {
                SectionHeader("Keyboard")
                KeyboardGridView(sessions: vm.sessions)
            }
            VStack(alignment: .leading, spacing: 8) {
                SectionHeader("Sessions")
                SessionsListView(sessions: vm.sessions)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct SectionHeader: View {
    let title: String
    init(_ title: String) { self.title = title }

    var body: some View {
        Text(title.uppercased())
            .font(.system(size: 11, weight: .medium))
            .foregroundColor(.textMuted)
            .tracking(0.5)
    }
}

// MARK: - Status Bar

struct StatusBarView: View {
    let status: StatusData

    var body: some View {
        HStack(spacing: 14) {
            Circle()
                .fill(status.connected ? Color.kbd_green : Color.kbd_red)
                .frame(width: 7, height: 7)

            Text(status.connected ? "Connected (\(status.protocol))" : "Disconnected")
                .font(.system(size: 12))
                .foregroundColor(.textDim)

            Divider().frame(height: 16)

            Label("Uptime", content: formatDuration(status.uptime_seconds))
            Divider().frame(height: 16)
            Label("Slots", content: "\(status.slots_used)/\(status.slots_total)")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color.surface)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 1))
        .cornerRadius(8)
    }

    func Label(_ label: String, content: String) -> some View {
        HStack(spacing: 6) {
            Text(label).font(.system(size: 12)).foregroundColor(.textMuted)
            Text(content).font(.system(size: 12, design: .monospaced)).foregroundColor(.textDim)
        }
    }
}

// MARK: - Keyboard Grid

struct KeyboardGridView: View {
    let sessions: [SessionData]

    private var ledMap: [Int: SessionData] {
        var map: [Int: SessionData] = [:]
        for sess in sessions {
            if sess.slot < SLOT_LEDS.count {
                map[SLOT_LEDS[sess.slot]] = sess
            }
        }
        return map
    }

    var body: some View {
        VStack(spacing: 4) {
            ForEach(0..<4, id: \.self) { row in
                HStack(spacing: 4) {
                    ForEach(0..<4, id: \.self) { col in
                        let led = LAYOUT[row][col]
                        KeyCellView(led: led, session: led.flatMap { ledMap[$0] })
                    }
                }
            }
            // Underglow bar
            UnderglowBar()
        }
    }
}

struct KeyCellView: View {
    let led: Int?
    let session: SessionData?

    private var isSlotLED: Bool {
        guard let led else { return false }
        return SLOT_LEDS.contains(led)
    }

    var body: some View {
        TimelineView(.animation) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            cellContent(time: t)
        }
        .frame(width: 64, height: 64)
    }

    @ViewBuilder
    func cellContent(time: Double) -> some View {
        let isStale = session.map { $0.idle_seconds > DIM_TIMEOUT } ?? false
        let state = session?.state ?? ""

        ZStack {
            // Background fill with animation
            RoundedRectangle(cornerRadius: 8)
                .fill(bgFill(state: state, isStale: isStale, time: time))

            // Glow shadow for your_turn
            if state == "your_turn" && !isStale {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.clear)
                    .shadow(color: .kbd_orange.opacity(glowOpacity(time: time, period: 2)), radius: 12)
            }

            // Border
            RoundedRectangle(cornerRadius: 8)
                .stroke(borderColor(state: state, isStale: isStale), lineWidth: 1.5)

            // Label content
            VStack(spacing: 2) {
                if let led {
                    Text("\(led)")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(session != nil || !isSlotLED ? .textPrimary : .textMuted)
                    if session != nil {
                        Text("slot \(session!.slot)")
                            .font(.system(size: 9))
                            .foregroundColor(.textMuted)
                    }
                }
            }
        }
        .overlay(
            led == nil ?
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [4, 4]))
                    .foregroundColor(Color(white: 0.1))
                : nil
        )
    }

    func bgFill(state: String, isStale: Bool, time: Double) -> Color {
        // Non-slot LEDs (10, 11, 1, 0): permanent subtle glow
        if led != nil && !isSlotLED {
            return .kbd_orange.opacity(0.12)
        }
        if session == nil { return .clear }
        if isStale { return .kbd_orange.opacity(0.03) }

        switch state {
        case "your_turn", "acknowledged":
            // 2s sinusoidal pulse, opacity 0.08–0.30
            let phase = sinPhase(time: time, period: 2)
            let opacity = 0.08 + (0.30 - 0.08) * phase
            return .kbd_orange.opacity(opacity)
        case "working":
            // 3s sinusoidal breathe, opacity 0.05–0.22
            let phase = sinPhase(time: time, period: 3)
            let opacity = 0.05 + (0.22 - 0.05) * phase
            return .kbd_orange.opacity(opacity)
        default:
            return .clear
        }
    }

    func borderColor(state: String, isStale: Bool) -> Color {
        // Non-slot LEDs: permanent orange border
        if led != nil && !isSlotLED { return .kbd_orange.opacity(0.3) }
        guard session != nil else { return .border }
        if isStale { return .kbd_orange.opacity(0.15) }
        if !state.isEmpty { return .kbd_orange }
        return .border
    }

    func glowOpacity(time: Double, period: Double) -> Double {
        let phase = sinPhase(time: time, period: period)
        return phase * 0.3
    }

    /// Sinusoidal 0→1→0 matching firmware: (sin(t/period * 2π - π/2) + 1) / 2
    func sinPhase(time: Double, period: Double) -> Double {
        (sin(time / period * 2 * .pi - .pi / 2) + 1) / 2
    }
}

struct UnderglowBar: View {
    var body: some View {
        TimelineView(.animation) { context in
            let phase = (sin(context.date.timeIntervalSinceReferenceDate / 4 * 2 * .pi - .pi / 2) + 1) / 2
            RoundedRectangle(cornerRadius: 3)
                .fill(Color.kbd_orange.opacity(0.15))
                .frame(height: 6)
                .opacity(0.3 + 0.7 * phase)
        }
    }
}

// MARK: - Sessions List

struct SessionsListView: View {
    let sessions: [SessionData]

    var body: some View {
        if sessions.isEmpty {
            Text("No active sessions")
                .font(.system(size: 12))
                .foregroundColor(.textMuted)
                .frame(maxWidth: .infinity)
                .padding(24)
                .background(Color.surface)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 1))
                .cornerRadius(8)
        } else {
            VStack(spacing: 8) {
                ForEach(sessions) { session in
                    SessionCardView(session: session)
                }
            }
        }
    }
}

struct SessionCardView: View {
    let session: SessionData

    var stateLabel: String {
        switch session.state {
        case "your_turn": return "Your turn"
        case "acknowledged": return "Acknowledged"
        default: return "Working"
        }
    }

    var body: some View {
        TimelineView(.animation) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            cardContent(time: t)
        }
    }

    func cardContent(time: Double) -> some View {
        HStack(spacing: 12) {
            // Slot badge
            slotBadge(time: time)

            // Info
            VStack(alignment: .leading, spacing: 2) {
                Text(stateLabel)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(session.state == "your_turn" || session.state == "acknowledged" ? .kbd_orange : .textDim)

                HStack(spacing: 0) {
                    Text("LED \(session.led_index)")
                    Text(" · ")
                    Text(String(session.session_id.prefix(8)))
                    if !session.iterm_session.isEmpty {
                        Text(" · ")
                        Text(String(session.iterm_session.suffix(12)))
                    }
                }
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(.textMuted)
                .lineLimit(1)
            }

            Spacer()

            Text(formatDuration(session.idle_seconds))
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(.textMuted)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color.surface)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 1))
        .cornerRadius(8)
    }

    func slotBadge(time: Double) -> some View {
        let isWorking = session.state == "working"
        let phase = isWorking ? (sin(time / 3 * 2 * .pi - .pi / 2) + 1) / 2 : 1.0
        let bgOpacity = isWorking ? 0.08 + (0.25 - 0.08) * phase : 1.0

        return Text("\(session.slot)")
            .font(.system(size: 12, weight: .semibold, design: .monospaced))
            .foregroundColor(isWorking ? Color.kbd_orange : Color.white)
            .frame(width: 28, height: 28)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.kbd_orange.opacity(bgOpacity))
            )
    }
}

// MARK: - Event Log

struct EventLogView: View {
    let events: [EventData]
    @Binding var filterText: String
    @State private var autoScroll = true
    @State private var scrollTarget: String?

    private let columns = [
        ("Time", CGFloat(72)),
        ("Session", CGFloat(72)),
        ("Event", CGFloat(0)),  // flexible
        ("Tool", CGFloat(140)),
    ]

    var body: some View {
        VStack(spacing: 0) {
            // Header with filter
            HStack {
                SectionHeader("Event Log")
                Spacer()
                TextField("Filter events...", text: $filterText)
                    .textFieldStyle(.plain)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(.textDim)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Color.bg)
                    .overlay(
                        RoundedRectangle(cornerRadius: 4)
                            .stroke(filterText.isEmpty ? Color.border : Color.kbd_orange, lineWidth: 1)
                    )
                    .frame(width: 180)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)

            Divider().background(Color.border)

            // Column headers
            HStack(spacing: 0) {
                ForEach(columns, id: \.0) { col in
                    Text(col.0.uppercased())
                        .font(.system(size: 10, weight: .medium))
                        .foregroundColor(.textMuted)
                        .tracking(0.4)
                        .frame(width: col.1 == 0 ? nil : col.1, alignment: .leading)
                        .frame(maxWidth: col.1 == 0 ? .infinity : nil, alignment: .leading)
                        .padding(.horizontal, 10)
                }
            }
            .padding(.vertical, 6)
            .background(Color.surface)

            Divider().background(Color.border)

            // Event rows
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(Array(events.enumerated()), id: \.offset) { idx, event in
                            EventRowView(event: event)
                                .id(idx)
                        }
                    }
                }
                .frame(maxHeight: 300)
                .onChange(of: events.count) { _, _ in
                    if autoScroll && !events.isEmpty {
                        proxy.scrollTo(events.count - 1, anchor: .bottom)
                    }
                }
            }
        }
        .background(Color.surface)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 1))
        .cornerRadius(8)
    }
}

struct EventRowView: View {
    let event: EventData

    var body: some View {
        HStack(spacing: 0) {
            Text(formatTime(event.ts))
                .frame(width: 72, alignment: .leading)
                .padding(.horizontal, 10)

            Text(String((event.session ?? "").prefix(8)))
                .frame(width: 72, alignment: .leading)
                .padding(.horizontal, 10)

            eventBadge
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 10)

            Text(event.tool ?? "")
                .frame(width: 140, alignment: .leading)
                .padding(.horizontal, 10)
        }
        .font(.system(size: 11, design: .monospaced))
        .foregroundColor(.textDim)
        .padding(.vertical, 4)
        .background(Color.surface)
        .overlay(
            Rectangle()
                .fill(Color(white: 0.07))
                .frame(height: 1),
            alignment: .bottom
        )
    }

    var eventBadge: some View {
        let label: String
        if let notif = event.notif, !notif.isEmpty {
            label = "\(event.event ?? "") (\(notif))"
        } else {
            label = event.event ?? ""
        }

        return Text(label)
            .font(.system(size: 10, design: .monospaced))
            .foregroundColor(eventColor(event.event, event.notif))
            .padding(.horizontal, 6)
            .padding(.vertical, 1)
            .background(
                RoundedRectangle(cornerRadius: 3)
                    .fill(eventBgColor(event.event, event.notif))
            )
    }
}

// MARK: - Stats Bar

struct StatsBarView: View {
    let counts: [(String, Int)]
    let avgResponse: Double?

    var body: some View {
        HStack(spacing: 12) {
            ForEach(counts, id: \.0) { item in
                StatView(label: item.0, value: "\(item.1)")
            }
            if let avg = avgResponse {
                StatView(label: "Avg response", value: formatDuration(avg), isOrange: true)
            }
        }
    }
}

struct StatView: View {
    let label: String
    let value: String
    var isOrange = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label.uppercased())
                .font(.system(size: 10))
                .foregroundColor(.textMuted)
                .tracking(0.4)
            Text(value)
                .font(.system(size: 18, weight: .semibold, design: .monospaced))
                .foregroundColor(isOrange ? .kbd_orange : .textPrimary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color.surface)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.border, lineWidth: 1))
        .cornerRadius(8)
    }
}

// MARK: - Root View (handles connection state switching)

struct RootView: View {
    @Bindable var vm: DashboardViewModel
    var onRetry: () -> Void

    var body: some View {
        switch vm.connectionState {
        case .connecting:
            ConnectingOverlay(message: "Connecting...")
        case .connected:
            DashboardView(vm: vm)
        case .error(let msg):
            ConnectingOverlay(message: msg, canRetry: true, onRetry: onRetry)
        }
    }
}

struct ConnectingOverlay: View {
    let message: String
    var canRetry: Bool = false
    var onRetry: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: 12) {
            Text(message)
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)

            if canRetry, let onRetry {
                Button("Retry") { onRetry() }
                    .buttonStyle(.bordered)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.bg)
    }
}

// MARK: - App Delegate

class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private let vm = DashboardViewModel()
    private var daemonProcess: Process?
    private var checkTimer: Timer?
    private var ownsDaemon = false
    private var stderrPipe = Pipe()

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMainMenu()
        setupWindow()
        vm.connectionState = .connecting
        checkExistingDaemon()
    }

    func applicationWillTerminate(_ notification: Notification) {
        checkTimer?.invalidate()
        vm.stopPolling()
        killDaemon()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }

    // MARK: Window

    func setupWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 900, height: 700),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "Keyboard Claude"
        window.center()
        window.minSize = NSSize(width: 600, height: 450)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = NSColor(red: 10/255, green: 10/255, blue: 10/255, alpha: 1)
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden

        let hostingView = NSHostingView(rootView: RootView(vm: vm) { [weak self] in
            self?.startDaemon()
        })
        window.contentView = hostingView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func setupMainMenu() {
        let mainMenu = NSMenu()

        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "About Keyboard Claude",
                                   action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
                                   keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Quit Keyboard Claude",
                                   action: #selector(NSApplication.terminate(_:)),
                                   keyEquivalent: "q"))
        let appMenuItem = NSMenuItem()
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        let editMenuItem = NSMenuItem()
        editMenuItem.submenu = editMenu
        mainMenu.addItem(editMenuItem)

        NSApp.mainMenu = mainMenu
    }

    // MARK: Daemon Management

    func killDaemon() {
        // Kill our process if we own it
        if ownsDaemon, let proc = daemonProcess, proc.isRunning {
            proc.terminate() // SIGTERM
        }
        // Also kill via PID file (covers crashes / force-quit scenarios)
        if let pidStr = try? String(contentsOfFile: PID_FILE, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
           let pid = Int32(pidStr), pid > 0 {
            kill(pid, SIGTERM)
        }
        try? FileManager.default.removeItem(atPath: PID_FILE)
    }

    /// Find and kill whatever is listening on the daemon port.
    func killProcessOnPort(completion: @escaping () -> Void) {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        task.arguments = ["-ti:\(DAEMON_PORT)"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = FileHandle.nullDevice

        do {
            try task.run()
            task.waitUntilExit()
        } catch {
            completion()
            return
        }

        let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let pids = output.split(separator: "\n").compactMap { Int32($0.trimmingCharacters(in: .whitespaces)) }

        guard !pids.isEmpty else { completion(); return }

        for pid in pids { kill(pid, SIGTERM) }
        try? FileManager.default.removeItem(atPath: PID_FILE)

        // Wait for processes to die
        var checks = 0
        Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { timer in
            checks += 1
            let anyAlive = pids.contains { kill($0, 0) == 0 }
            if !anyAlive || checks >= 30 {
                if anyAlive { for pid in pids { kill(pid, SIGKILL) } }
                timer.invalidate()
                completion()
            }
        }
    }

    func checkExistingDaemon() {
        killProcessOnPort { [weak self] in
            self?.startDaemon()
        }
    }

    func startDaemon() {
        let scriptPath = projectDir + "/vial_kbd.py"
        guard FileManager.default.fileExists(atPath: scriptPath) else {
            vm.connectionState = .error("vial_kbd.py not found at:\n\(scriptPath)")
            return
        }

        vm.connectionState = .connecting

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [scriptPath]
        process.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        process.standardOutput = FileHandle.nullDevice
        stderrPipe = Pipe()
        process.standardError = stderrPipe

        process.terminationHandler = { [weak self] proc in
            guard let self, self.ownsDaemon else { return }
            let data = self.stderrPipe.fileHandleForReading.readDataToEndOfFile()
            let stderr = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let lastLines = stderr.split(separator: "\n").suffix(3).joined(separator: "\n")
            DispatchQueue.main.async {
                self.vm.stopPolling()
                let msg = lastLines.isEmpty ? "Keyboard service stopped unexpectedly" : "Daemon error:\n\(lastLines)"
                self.vm.connectionState = .error(msg)
            }
        }

        do {
            try process.run()
            daemonProcess = process
            ownsDaemon = true
            try? "\(process.processIdentifier)".write(toFile: PID_FILE, atomically: true, encoding: .utf8)
            waitForDaemon()
        } catch {
            vm.connectionState = .error("Failed to start:\n\(error.localizedDescription)")
        }
    }

    func waitForDaemon() {
        var attempts = 0
        checkTimer?.invalidate()
        checkTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] timer in
            attempts += 1
            if attempts > 20 {
                timer.invalidate()
                self?.vm.connectionState = .error("Could not connect")
                return
            }
            let url = URL(string: "\(API_BASE)/api/status")!
            URLSession.shared.dataTask(with: url) { _, response, _ in
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    DispatchQueue.main.async {
                        timer.invalidate()
                        self?.vm.connectionState = .connected
                        self?.vm.startPolling()
                    }
                }
            }.resume()
        }
    }
}

// MARK: - Entry Point

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
