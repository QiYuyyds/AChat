"use client"

import * as React from "react"
import * as RechartsPrimitive from "recharts"

import { cn } from "@/lib/utils"

export type ChartConfig = Record<
  string,
  {
    label?: React.ReactNode
    icon?: React.ComponentType
    color?: string
  }
>

type ChartContextProps = {
  config: ChartConfig
}

const ChartContext = React.createContext<ChartContextProps | null>(null)

function useChart() {
  const context = React.useContext(ChartContext)
  if (!context) {
    throw new Error("useChart must be used within a <ChartContainer />")
  }
  return context
}

function ChartContainer({
  id,
  className,
  children,
  config,
  ...props
}: React.ComponentProps<"div"> & {
  config: ChartConfig
  children: React.ComponentProps<
    typeof RechartsPrimitive.ResponsiveContainer
  >["children"]
}) {
  const uniqueId = React.useId()
  const chartId = `chart-${id || uniqueId.replace(/:/g, "")}`

  return (
    <ChartContext.Provider value={{ config }}>
      <div
        data-slot="chart"
        data-chart={chartId}
        className={cn(
          "[&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-cartesian-grid_line]:stroke-border/50 [&_.recharts-curve.recharts-tooltip-cursor]:stroke-border [&_.recharts-polar-grid_angle-line]:stroke-border [&_.recharts-polar-grid_axis-line]:stroke-border [&_.recharts-polar-radius-axis-tick_text]:fill-muted-foreground [&_.recharts-rectangle.recharts-tooltip-cursor]:fill-muted/20 [&_.recharts-reference-line_line]:stroke-border flex aspect-video justify-center text-xs *:[svg]:size-full",
          className
        )}
        {...props}
      >
        <ChartStyle id={chartId} config={config} />
        <RechartsPrimitive.ResponsiveContainer>
          {children}
        </RechartsPrimitive.ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  )
}

const ChartStyle = React.memo(function ChartStyle({
  id,
  config,
}: {
  id: string
  config: ChartConfig
}) {
  const colorConfig = Object.entries(config).filter(
    ([, c]) => c.color || c.label
  )

  if (!colorConfig.length) {
    return null
  }

  const css = `
[data-chart="${id}"] {
${colorConfig
  .map(([key, itemConfig]) => {
    const color = itemConfig.color
    return color ? `  --color-${key}: ${color};` : null
  })
  .filter(Boolean)
  .join("\n")}
}`

  return (
    <style
      dangerouslySetInnerHTML={{ __html: css }}
    />
  )
})

function ChartTooltip({
  ...props
}: React.ComponentProps<typeof RechartsPrimitive.Tooltip>) {
  return (
    <RechartsPrimitive.Tooltip
      {...props}
    />
  )
}

interface TooltipPayloadItem {
  name?: string | number
  dataKey?: string | number
  value?: string | number | Array<string | number>
  color?: string
  payload?: Record<string, unknown>
}

function ChartTooltipContent({
  active,
  payload,
  className,
  indicator = "dot",
  hideLabel = false,
  hideIndicator = false,
  label,
  labelFormatter,
  labelClassName,
  color,
  nameKey,
  labelKey,
}: React.ComponentProps<"div"> & {
  active?: boolean
  payload?: TooltipPayloadItem[]
  hideLabel?: boolean
  hideIndicator?: boolean
  indicator?: "line" | "dot" | "dashed"
  nameKey?: string
  labelKey?: string
  label?: string
  labelFormatter?: (
    value: React.ReactNode,
    payload: TooltipPayloadItem[]
  ) => React.ReactNode
  labelClassName?: string
  color?: string
}) {
  const { config } = useChart()

  const tooltipLabel = React.useMemo(() => {
    if (hideLabel || !payload?.length) {
      return null
    }

    const item = payload[0]
    const key = `${labelKey || item.dataKey || item.name || "value"}`
    const itemConfig = getPayloadConfigFromPayload(config, item, key)
    const value =
      !labelKey && typeof label === "string"
        ? config[key]?.label || label
        : itemConfig?.label

    if (labelFormatter) {
      return (
        <div className={cn("font-medium", labelClassName)}>
          {labelFormatter(value, payload)}
        </div>
      )
    }

    if (!value) {
      return null
    }

    return <div className={cn("font-medium", labelClassName)}>{value}</div>
  }, [
    label,
    labelFormatter,
    payload,
    hideLabel,
    labelClassName,
    config,
    labelKey,
  ])

  if (!active || !payload?.length) {
    return null
  }

  const nestLabel = payload.length === 1 && indicator !== "dot"

  let renderLabel: React.ReactNode = tooltipLabel

  if (payload.length > 1 && !nestLabel) {
    renderLabel = null
  }

  return (
    <div
      className={cn(
        "border-border/50 bg-background grid min-w-[8rem] items-start gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs shadow-xl",
        className
      )}
    >
      {nestLabel ? (
        <div className="grid gap-1.5">
          {renderLabel}
          <div className="grid gap-1.5">
            {payload.map((item, index) => (
              <TooltipRow
                key={String(item.dataKey || index)}
                item={item}
                config={config}
                indicator={indicator}
                hideIndicator={hideIndicator}
                color={color}
                nameKey={nameKey}
              />
            ))}
          </div>
        </div>
      ) : (
        payload.map((item, index) => (
          <TooltipRow
            key={String(item.dataKey || index)}
            item={item}
            config={config}
            indicator={indicator}
            hideIndicator={hideIndicator}
            color={color}
            nameKey={nameKey}
          />
        ))
      )}
      {nestLabel ? null : renderLabel}
    </div>
  )
}

function TooltipRow({
  item,
  config,
  indicator,
  hideIndicator,
  color,
  nameKey,
}: {
  item: TooltipPayloadItem
  config: ChartConfig
  indicator: "line" | "dot" | "dashed"
  hideIndicator?: boolean
  color?: string
  nameKey?: string
}) {
  const key = `${nameKey || item.name || item.dataKey || "value"}`
  const itemConfig = getPayloadConfigFromPayload(config, item, key)
  const indicatorColor = color || (item.payload?.fill as string) || item.color

  return (
    <div
      className={cn(
        "[&>svg]:text-muted-foreground flex w-full flex-wrap items-stretch gap-2 [&>svg]:h-2.5 [&>svg]:w-2.5"
      )}
    >
      <>
        {itemConfig?.icon ? (
          <itemConfig.icon />
        ) : (
          !hideIndicator &&
          (indicator === "dot" ? (
            <div
              className="border-primary/30 bg-primary size-2 shrink-0 rounded-[2px]"
              style={{
                backgroundColor: indicatorColor,
                borderColor: indicatorColor,
              }}
            />
          ) : (
            <div
              className="bg-primary size-2 shrink-0 rounded-[2px]"
              style={{ backgroundColor: indicatorColor }}
            />
          ))
        )}
        <div className="flex flex-1 leading-none">
          {itemConfig?.label ? (
            <span className="text-muted-foreground">
              {itemConfig.label}
            </span>
          ) : null}
          {item.value !== undefined && (
            <span className="text-foreground ml-auto font-mono font-medium tabular-nums">
              {typeof item.value === "number"
                ? item.value.toLocaleString()
                : String(item.value)}
            </span>
          )}
        </div>
      </>
    </div>
  )
}

function ChartLegend({
  ...props
}: React.ComponentProps<typeof RechartsPrimitive.Legend>) {
  return (
    <RechartsPrimitive.Legend
      {...props}
    />
  )
}

interface LegendPayloadItem {
  value?: string | number
  dataKey?: string | number
  color?: string
}

function ChartLegendContent({
  className,
  hideIcon = false,
  payload,
  verticalAlign = "bottom",
  nameKey,
}: React.ComponentProps<"div"> & {
  payload?: LegendPayloadItem[]
  verticalAlign?: "top" | "bottom" | "middle"
  hideIcon?: boolean
  nameKey?: string
}) {
  const { config } = useChart()

  if (!payload?.length) {
    return null
  }

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-4 [&>svg]:size-3.5 [&>svg]:text-muted-foreground",
        verticalAlign === "top" ? "pb-3" : "pt-3",
        className
      )}
    >
      {payload.map((item, index) => {
        const key = `${nameKey || item.dataKey || "value"}`
        const itemConfig = getPayloadConfigFromPayload(config, item, key)

        return (
          <div
            key={String(item.value || index)}
            className="flex items-center gap-1.5 [&>svg]:size-3 [&>svg]:text-muted-foreground"
          >
            {itemConfig?.icon && !hideIcon ? (
              <itemConfig.icon />
            ) : (
              <div
                className="size-2 shrink-0 rounded-[2px]"
                style={{ backgroundColor: item.color }}
              />
            )}
            {itemConfig?.label}
          </div>
        )
      })}
    </div>
  )
}

function getPayloadConfigFromPayload(
  config: ChartConfig,
  payload: unknown,
  key: string
): ChartConfig[string] | undefined {
  if (typeof payload !== "object" || payload === null) {
    return undefined
  }

  const payloadPayload =
    "payload" in payload &&
    typeof (payload as Record<string, unknown>).payload === "object" &&
    (payload as Record<string, unknown>).payload !== null
      ? ((payload as Record<string, unknown>).payload as Record<string, unknown>)
      : undefined

  let configLabelKey: string = key

  if (
    payloadPayload &&
    Object.keys(payloadPayload).length > 0 &&
    payloadPayload
  ) {
    if (key in payloadPayload) {
      configLabelKey = key
    } else {
      const firstKey = Object.keys(payloadPayload)[0]
      if (firstKey) {
        configLabelKey = firstKey
      }
    }
  }

  return configLabelKey in config
    ? config[configLabelKey]
    : undefined
}

export {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
  ChartStyle,
}
