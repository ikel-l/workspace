import QtQuick
import QtQuick.Layouts
import "../Ui"
import "../Components"

PageScaffold {
    id: page
    objectName: "pageTransport"
    title: qsTr("Transport")
    subtitle: qsTr("Parameters for connecting to desktops for profile «%1»").arg(client.currentProfile.label || client.username)
    showFooter: false
    property int tab: 0
    readonly property bool twoColumns: contentWidth >= Theme.columnsBreakpoint
    readonly property bool available: client.spiceSettings.available === true

    SegmentedTabs {
        objectName: "transportTabs"
        model: [qsTr("SPICE"), qsTr("Gorizont-VS Protocol")]
        currentIndex: page.tab
        onSelected: function(index) { page.tab = index }
    }

    Notice { visible: !page.available; kind: "warning"; title: qsTr("No active connection"); text: qsTr("Transport settings are bound to the saved connection.") }

    SpiceTransportSettings { visible: page.tab === 0; twoColumns: page.twoColumns }
    GorizontVSTransportSettings { visible: page.tab === 1; twoColumns: page.twoColumns }

    Notice {
        kind: "info"
        iconPaths: Icons.info
        title: qsTr("When settings are applied")
        text: page.tab === 0
              ? qsTr("Parameters are used when connecting to the desktop via SPICE.")
              : qsTr("Parameters are used when connecting via Gorizont-VS Protocol. Values marked «Server default» are taken from the administrator's configuration.")
    }
}
