import QtQuick
import QtQuick.Layouts
import "../Ui"

GridLayout {
    id: root
    objectName: "gorizontvsTransportSettings"
    property bool twoColumns: true
    readonly property var model: client.GorizontVSSettings

    Layout.fillWidth: true
    enabled: model.available === true
    columns: twoColumns ? 2 : 1
    columnSpacing: 24
    rowSpacing: 24

    function save(patch) {
        var next = {}
        for (var k in root.model) next[k] = root.model[k]
        for (var p in patch) next[p] = patch[p]
        client.saveGorizontVSSettings(next)
    }
    function pretty(resolution) { return String(resolution).replace("x", " × ") }

    readonly property var resolutionOptions: [
        { value: "", label: qsTr("Server default") },
        { value: "native", label: model.nativeResolution ? qsTr("Native — %1").arg(pretty(model.nativeResolution)) : qsTr("Native (screen resolution)") },
        { value: "1280x720", label: "1280 × 720 (720p)" },
        { value: "1920x1080", label: "1920 × 1080 (1080p)" },
        { value: "2560x1440", label: "2560 × 1440 (1440p)" },
        { value: "3840x2160", label: "3840 × 2160 (4K)" }
    ]
    readonly property var fpsOptions: [
        { value: "0", label: qsTr("Server default") },
        { value: "30", label: "30" }, { value: "60", label: "60" }, { value: "90", label: "90" },
        { value: "120", label: "120" }, { value: "144", label: "144" }, { value: "240", label: "240" }
    ]

    Card {
        objectName: "gorizontvsImageCard"
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        title: qsTr("Image")
        subtitle: qsTr("Resolution, frame rate and quality of the stream")
        FormGrid {
            Layout.fillWidth: true
            AppField {
                Layout.fillWidth: true
                kind: "select"
                label: qsTr("Resolution")
                options: root.resolutionOptions
                value: root.model.resolution || ""
                onEdited: function(text) { root.save({ resolution: text }) }
            }
            AppField {
                Layout.fillWidth: true
                kind: "select"
                label: qsTr("FPS")
                options: root.fpsOptions
                value: String(root.model.fps || 0)
                onEdited: function(text) { root.save({ fps: Number(text) }) }
            }
        }
        AppSlider {
            objectName: "gorizontvsBitrate"
            Layout.fillWidth: true
            label: qsTr("Bitrate")
            unit: qsTr("Mbit/s")
            from: 1; to: 150; step: 1
            defaultValue: 20
            hint: qsTr("Default — the bitrate configured by the administrator. Higher values improve quality but need a faster network.")
            value: root.model.bitrate > 0 ? String(Math.round(root.model.bitrate / 1000)) : ""
            onEdited: function(text) { root.save({ bitrate: text === "" ? 0 : Math.round(Number(text) * 1000) }) }
        }
        AppField {
            Layout.fillWidth: true
            kind: "select"
            label: qsTr("Display mode")
            options: [
                { value: "fullscreen", label: qsTr("Full screen") },
                { value: "borderless", label: qsTr("Borderless window") },
                { value: "windowed", label: qsTr("Windowed") }
            ]
            value: root.model.displayMode || "fullscreen"
            onEdited: function(text) { root.save({ displayMode: text }) }
        }
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Vertical sync")
            description: qsTr("Prevents image tearing; adds a little latency.")
            checked: root.model.vsync === true
            onToggled: function(v) { root.save({ vsync: v }) }
        }
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Frame pacing")
            description: qsTr("Smooths frame delivery to the display refresh rate.")
            checked: root.model.framePacing === true
            onToggled: function(v) { root.save({ framePacing: v }) }
        }
    }

    Card {
        objectName: "gorizontvsAudioCard"
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        title: qsTr("Audio")
        subtitle: qsTr("Sound of the remote session")
        AppField {
            Layout.fillWidth: true
            kind: "select"
            label: qsTr("Audio channels")
            options: [
                { value: "stereo", label: qsTr("Stereo") },
                { value: "5.1-surround", label: qsTr("5.1 surround") },
                { value: "7.1-surround", label: qsTr("7.1 surround") }
            ]
            value: root.model.audioConfig || "stereo"
            onEdited: function(text) { root.save({ audioConfig: text }) }
        }
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Do not play audio on the host PC")
            description: qsTr("Sound is played only on this computer while the session is active.")
            checked: root.model.muteHostAudio === true
            onToggled: function(v) { root.save({ muteHostAudio: v }) }
        }
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Mute audio when the session is in the background")
            checked: root.model.muteOnFocusLoss === true
            onToggled: function(v) { root.save({ muteOnFocusLoss: v }) }
        }
    }

    Card {
        objectName: "gorizontvsInputCard"
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        title: qsTr("Input")
        subtitle: qsTr("Mouse and keyboard")
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Optimize mouse for remote desktop instead of games")
            description: qsTr("The cursor is not captured and moves exactly as on the local screen.")
            checked: root.model.absoluteMouse === true
            onToggled: function(v) { root.save({ absoluteMouse: v }) }
        }
        AppField {
            Layout.fillWidth: true
            kind: "select"
            label: qsTr("Capture system hotkeys")
            hint: qsTr("Alt+Tab, Win and other system shortcuts are sent to the remote desktop.")
            options: [
                { value: "never", label: qsTr("Never") },
                { value: "fullscreen", label: qsTr("In full screen only") },
                { value: "always", label: qsTr("Always") }
            ]
            value: root.model.captureSystemKeys || "never"
            onEdited: function(text) { root.save({ captureSystemKeys: text }) }
        }
    }

    Card {
        objectName: "gorizontvsAdvancedCard"
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        title: qsTr("Advanced settings")
        subtitle: qsTr("Decoding and diagnostics")
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Show performance statistics while streaming")
            checked: root.model.performanceOverlay === true
            onToggled: function(v) { root.save({ performanceOverlay: v }) }
        }
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Automatically detect connection problems")
            checked: root.model.detectNetworkBlocking === true
            onToggled: function(v) { root.save({ detectNetworkBlocking: v }) }
        }
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Keep the screen on while streaming")
            checked: root.model.keepAwake === true
            onToggled: function(v) { root.save({ keepAwake: v }) }
        }
        FormGrid {
            Layout.fillWidth: true
            AppField {
                Layout.fillWidth: true
                kind: "select"
                label: qsTr("Video decoder")
                options: [
                    { value: "auto", label: qsTr("Automatic") },
                    { value: "software", label: qsTr("Software") },
                    { value: "hardware", label: qsTr("Hardware") }
                ]
                value: root.model.videoDecoder || "auto"
                onEdited: function(text) { root.save({ videoDecoder: text }) }
            }
            AppField {
                Layout.fillWidth: true
                kind: "select"
                label: qsTr("Video codec")
                options: [
                    { value: "auto", label: qsTr("Automatic") },
                    { value: "H.264", label: "H.264" },
                    { value: "HEVC", label: "HEVC (H.265)" },
                    { value: "AV1", label: "AV1" }
                ]
                value: root.model.videoCodec || "auto"
                onEdited: function(text) { root.save({ videoCodec: text }) }
            }
        }
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Enable HDR")
            description: qsTr("Requires an HDR-capable display and a host with HDR enabled.")
            checked: root.model.hdr === true
            onToggled: function(v) { root.save({ hdr: v }) }
        }
    }
}
