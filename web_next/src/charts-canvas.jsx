import { useEffect, useRef } from "react";
import { init, use } from "echarts/core";
import { BarChart, FunnelChart, HeatmapChart, LineChart, RadarChart } from "echarts/charts";
import { SunburstChart } from "echarts/charts";
import { GridComponent, LegendComponent, RadarComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

use([
  BarChart,
  FunnelChart,
  HeatmapChart,
  LineChart,
  RadarChart,
  SunburstChart,
  GridComponent,
  LegendComponent,
  RadarComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export default function ChartCanvas({ option, onPick }) {
  const ref = useRef(null);
  const chart = useRef(null);
  useEffect(() => {
    if (!ref.current) return undefined;
    chart.current = init(ref.current, undefined, { renderer: "canvas" });
    const onClick = (ev) => {
      const raw = ev?.data;
      const href = raw?.href || (Array.isArray(raw) ? raw[3] : raw?.value?.[3]);
      if (href && onPick) onPick(href);
    };
    chart.current.on("click", onClick);
    const onResize = () => chart.current && chart.current.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.current && chart.current.off("click", onClick);
      chart.current && chart.current.dispose();
      chart.current = null;
    };
  }, [onPick]);
  useEffect(() => {
    if (chart.current && option) chart.current.setOption(option, true);
  }, [option]);
  return <div ref={ref} className="fs-chart-canvas" />;
}
