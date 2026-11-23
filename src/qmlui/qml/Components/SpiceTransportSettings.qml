import QtQuick
import QtQuick.Layouts
import "../Ui"

GridLayout {
    id: root
    objectName: "spiceTransportSettings"
    property bool twoColumns: true
    readonly property var spice: client.spiceSettings

    Layout.fillWidth: true
    enabled: spice.available === true
    columns: twoColumns ? 2 : 1
    columnSpacing: 24
    rowSpacing: 24

    function save(patch) {
        var next = { multiMonitorEnabled: spice.multiMonitorEnabled, monitorCount: spice.monitorCount,
                     webdavEnabled: spice.webdavEnabled, webdavPath: spice.webdavPath }
        for (var k in patch) next[k] = patch[k]
        client.saveSpiceSettings(next)
    }

    Card {
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        title: qsTr("Screen")
        subtitle: qsTr("SPICE: using multiple monitors")
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Multiple monitors")
            description: qsTr("The desktop will be spread across the specified number of monitors.")
            checked: root.spice.multiMonitorEnabled === true
            onToggled: function(v) { root.save({ multiMonitorEnabled: v }) }
        }
        AppSlider {
            Layout.fillWidth: true
            enabled: root.spice.multiMonitorEnabled === true
            label: qsTr("Number of monitors")
            from: 1; to: 4; step: 1
            defaultValue: 1
            value: String(root.spice.monitorCount || 1)
            onEdited: function(text) { root.save({ monitorCount: text === "" ? 1 : Number(text) }) }
        }
    }
    Card {
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        title: qsTr("Shared folder")
        subtitle: qsTr("SPICE: folder of this computer, available inside the desktop (WebDAV)")
        AppCheckBox {
            Layout.fillWidth: true
            label: qsTr("Connect shared folder")
            checked: root.spice.webdavEnabled === true
            onToggled: function(v) { root.save({ webdavEnabled: v }) }
        }
        RowLayout {
            Layout.fillWidth: true
            enabled: root.spice.webdavEnabled === true
            spacing: 10
            AppField {
                id: pathField
                Layout.fillWidth: true
                label: qsTr("Folder")
                placeholderText: qsTr("Not selected")
                value: root.spice.webdavPath || ""
                onEdited: function(text) { root.save({ webdavPath: text }) }
            }
            AppButton {
                Layout.alignment: Qt.AlignBottom
                implicitHeight: Theme.fieldHeight
                iconPaths: Icons.folder
                text: qsTr("Browse…")
                onClicked: { var chosen = client.browseFolder(pathField.text); if (chosen.length > 0) root.save({ webdavPath: chosen }) }
            }
        }
    }
}
