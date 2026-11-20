package leet.common

/** Framebuffer dimensions for the demo renderer. `viewH = 0` uses the full height; a positive value
  * reserves the remaining rows for the dashboard. BBC display timing lives in BbcMachine.
  */
case class RenderConfig(
    screenW: Int = 320,
    screenH: Int = 256,
    viewH: Int = 0,
    colorBits: Int = 2
) {
  def pixels: Int = screenW * screenH
  def viewHeight: Int = if (viewH <= 0) screenH else viewH
}

/** VGA timing in pixels and lines. The caller supplies the pixel clock enable. */
case class VideoTiming(
    hActive: Int = 640,
    hFront: Int = 16,
    hSync: Int = 96,
    hBack: Int = 48,
    vActive: Int = 480,
    vFront: Int = 10,
    vSync: Int = 2,
    vBack: Int = 33,
    hSyncPos: Boolean = false,
    vSyncPos: Boolean = false
) {
  def hTotal: Int = hActive + hFront + hSync + hBack
  def vTotal: Int = vActive + vFront + vSync + vBack
}
