#!/usr/bin/env bash
# Strip the Qt6 modules winpodx never uses from a bundled PySide6 install.
#
# winpodx's GUI imports ONLY these five Qt modules (verified by an AST/grep
# sweep of src/winpodx — keep this list in sync with that):
#     QtCore  QtGui  QtWidgets  QtSvg  QtDBus
# PySide6 wheels ship the *entire* Qt6 stack — QtWebEngine (the ~100 MB+
# giant: a full Chromium), QtQuick/QtQml, Qt3D, QtCharts, QtMultimedia,
# QtPdf, QtDataVisualization, … — none of which winpodx links. That is the
# real reason the "Thin" AppImage was still ~270 MB: dropping the bundled
# podman stack only saved ~20 MB; Qt is the bulk.
#
# This prunes by an explicit DENYLIST of unused modules (not an allowlist),
# so transitive dependencies of the five kept modules survive untouched, and
# the runtime essentials (the xcb / wayland platform plugins, the SVG image
# format + icon engine, platform themes, TLS) are kept by construction.
#
# Usage: slim-pyside6.sh <path-to-PySide6-dir>
set -euo pipefail

PYSIDE="${1:?usage: slim-pyside6.sh <path-to-PySide6-dir>}"
[ -d "$PYSIDE" ] || { echo "slim-pyside6: not a directory: $PYSIDE" >&2; exit 1; }
QT="$PYSIDE/Qt"

before_kb=$(du -sk "$PYSIDE" | cut -f1)

# Qt module basenames winpodx never imports. For each, remove the Python
# binding (PySide6/Qt<Mod>.abi3.so) and the Qt library
# (PySide6/Qt/lib/libQt6<Mod>.so*). KEPT (do NOT list here): Core, Gui,
# Widgets, Svg, DBus (used) + Network, OpenGL, OpenGLWidgets, PrintSupport,
# Xml, Concurrent (small, possible transitive deps of platform plugins).
DROP_MODULES=(
  WebEngineCore WebEngineWidgets WebEngineQuick WebEngineQuickDelegatesQml
  WebChannel WebChannelQuick WebSockets WebView
  Qml QmlModels QmlWorkerScript QmlLocalStorage QmlXmlListModel QmlCore
  QmlMeta QmlCompiler QmlToolingSettings QmlIntegration QmlAssetDownloader
  Quick QuickWidgets QuickControls2 QuickControls2Impl QuickTemplates2
  QuickLayouts QuickParticles QuickShapes QuickDialogs2 QuickDialogs2Utils
  QuickDialogs2QuickImpl QuickTest QuickEffects QuickTimeline QuickVectorImage
  QuickVectorImageGenerator
  3DCore 3DRender 3DInput 3DLogic 3DAnimation 3DExtras 3DQuick 3DQuickScene2D
  3DQuickExtras 3DQuickRender 3DQuickInput 3DQuickAnimation 3DQuickScene3D
  Quick3D Quick3DRuntimeRender Quick3DUtils Quick3DAssetImport Quick3DAssetUtils
  Quick3DEffects Quick3DHelpers Quick3DParticles Quick3DParticleEffects
  Quick3DPhysics Quick3DPhysicsHelpers Quick3DXr Quick3DSpatialAudio Quick3DGlslParser
  Charts ChartsQml DataVisualization DataVisualizationQml Graphs GraphsWidgets
  Multimedia MultimediaWidgets MultimediaQuick SpatialAudio
  Pdf PdfWidgets PdfQuick
  Sql Test Designer DesignerComponents UiTools UiPlugin Help
  Bluetooth Nfc Sensors SensorsQuick SerialPort SerialBus
  Positioning PositioningQuick Location
  NetworkAuth RemoteObjects RemoteObjectsQml Scxml ScxmlQml StateMachine StateMachineQml
  TextToSpeech HttpServer Coap Mqtt
  VirtualKeyboard VirtualKeyboardSettings
  ShaderTools Insight InsightTracker
)

for mod in "${DROP_MODULES[@]}"; do
  rm -f  "$PYSIDE/Qt${mod}.abi3.so"
  rm -f  "$PYSIDE/Qt${mod}.pyi"
  rm -f  "$QT/lib/libQt6${mod}.so"*
done

# Big data / resource trees that only the dropped modules need.
rm -rf "$QT/qml"            # QML modules (QtQuick/QtQml gone)
rm -rf "$QT/resources"      # QtWebEngine .pak / icudtl resources (~100 MB)
rm -rf "$QT/translations"   # Qt + WebEngine .qm catalogs (winpodx UI is English / own i18n)
rm -f  "$QT/libexec/QtWebEngineProcess"

# FFmpeg libraries are bundled solely for QtMultimedia (dropped above), so
# they are orphaned now — nothing left in the bundle links them (~22 MB).
rm -f  "$QT/lib"/lib{avcodec,avformat,avutil,avfilter,avdevice,swscale,swresample}.so*

# Plugin categories that belong only to dropped modules. KEEP: platforms,
# platformthemes, imageformats, iconengines (SVG icons!), wayland-*,
# xcbglintegrations, egldeviceintegrations, generic, tls, styles,
# platforminputcontexts.
for plug in multimedia sqldrivers webview position sensors texttospeech \
            assetimporters geometryloaders renderplugins renderers sceneparsers \
            qmltooling scxmldatamodel designer virtualkeyboard canbus gamepads \
            qmllint help; do
  rm -rf "$QT/plugins/$plug"
done

# PySide6 / Qt developer tooling + build cruft never needed at runtime.
rm -rf "$PYSIDE/Designer" "$PYSIDE/examples" "$PYSIDE/include" "$PYSIDE/typesystems" \
       "$PYSIDE/glue" "$PYSIDE/support" "$PYSIDE/scripts" "$PYSIDE/qml" \
       "$QT/include" 2>/dev/null || true
rm -f  "$QT/lib"/*.la "$QT/lib"/cmake -r 2>/dev/null || true
rm -rf "$QT/lib/cmake" "$QT/lib/pkgconfig" "$QT/lib/metatypes" "$QT/lib/objects-Release"
# Qt command-line tools (designer, assistant, qml*, lupdate, lrelease, qsb, balsam, …)
find "$PYSIDE" -maxdepth 1 -type f \( \
        -name 'assistant'  -o -name 'designer'   -o -name 'linguist' -o \
        -name 'lupdate'    -o -name 'lrelease'   -o -name 'lconvert' -o \
        -name 'qmlformat'  -o -name 'qmllint'    -o -name 'qmlls'    -o \
        -name 'qmlimportscanner' -o -name 'qmlcachegen' -o -name 'qmltyperegistrar' -o \
        -name 'qmlprofiler' -o -name 'qmlscene'  -o -name 'qmltestrunner' -o \
        -name 'qsb'        -o -name 'balsam'     -o -name 'balsamui'  -o \
        -name 'shadergen'  -o -name 'rcc'        -o -name 'uic'       -o \
        -name 'qmltyperegistrar' -o -name 'svgtoqml' -o -name 'qmlaotstats' \
     \) -delete 2>/dev/null || true
rm -rf "$QT/bin"

after_kb=$(du -sk "$PYSIDE" | cut -f1)
echo "[slim-pyside6] PySide6: $((before_kb/1024)) MB -> $((after_kb/1024)) MB (saved $(((before_kb-after_kb)/1024)) MB)"
