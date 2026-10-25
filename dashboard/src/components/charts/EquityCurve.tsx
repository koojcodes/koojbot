"use client";

import { useEffect, useRef } from "react";
import { createChart, AreaSeries, type IChartApi, type ISeriesApi, type UTCTimestamp, ColorType } from "lightweight-charts";

interface DataPoint {
  time: string;
  value: number;
}

interface EquityCurveProps {
  data: DataPoint[];
  height?: number;
}

export function EquityCurve({ data, height = 300 }: EquityCurveProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#737373",
        fontFamily: "var(--font-geist-mono)",
      },
      grid: {
        vertLines: { color: "#1a1a1a" },
        horzLines: { color: "#1a1a1a" },
      },
      rightPriceScale: {
        borderColor: "#262626",
      },
      timeScale: {
        borderColor: "#262626",
        timeVisible: true,
      },
      crosshair: {
        horzLine: { color: "#404040" },
        vertLine: { color: "#404040" },
      },
    });

    // lightweight-charts v5 API: pass the series class directly
    const series = chart.addSeries(AreaSeries, {
      lineColor: "#22c55e",
      topColor: "rgba(34, 197, 94, 0.3)",
      bottomColor: "rgba(34, 197, 94, 0.0)",
      lineWidth: 2,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);
    handleResize();

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [height]);

  useEffect(() => {
    if (seriesRef.current && data.length > 0) {
      // Convert ISO datetime strings to Unix timestamps (seconds) — required by lightweight-charts
      const formatted = data.map((d) => ({
        time: Math.floor(new Date(d.time).getTime() / 1000) as UTCTimestamp,
        value: d.value,
      }));
      // Sort ascending by time (required by the library)
      formatted.sort((a, b) => (a.time as number) - (b.time as number));
      seriesRef.current.setData(formatted);
    }
  }, [data]);

  return <div ref={containerRef} className="w-full" />;
}
