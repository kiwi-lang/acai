import { FC, useCallback, useRef, useState, useEffect, useMemo, DragEvent, CSSProperties } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Panel,
  addEdge,
  useNodesState,
  useEdgesState,
  Connection,
  Node,
  Edge,
  NodeTypes,
  EdgeTypes,
  ReactFlowInstance,
  Handle,
  Position,
  NodeProps,
  BaseEdge,
  getBezierPath,
  EdgeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Box,
  Button,
  HStack,
  VStack,
  Text,
  Input,
  Textarea,
  IconButton,
} from '@chakra-ui/react';
import {
  listWorkflows,
  getWorkflow,
  saveWorkflow,
  updateWorkflow,
  deleteWorkflow,
  saveBuiltinWorkflow,
  getNodeTypes,
  listConversations,
  listAgents,
  getAgentInputs,
  validateWorkflow,
  type WorkflowSummary,
  type WorkflowSpec,
  type NodeTypeDef,
  type PinDef,
  type ValidationError,
} from '../services/api';
import ChatPanel from './ChatPanel';

/* ================================================================== */
/*  Palette — Blender-inspired industrial colors                       */
/* ================================================================== */

const C = {
  bg:        '#1d1d1d',
  node:      '#2d2d2d',
  header:    '#333',
  border:    '#444',
  text:      '#ccc',
  muted:     '#888',
  green:     '#7fba55',
  blue:      '#5b9bd5',
  amber:     '#d4a44c',
  cyan:      '#5cc6c6',
  red:       '#c45555',
  purple:    '#9b7ed0',
  white:     '#ccc',
  grid:      '#2a2a2a',
} as const;

type PinColor = typeof C[keyof typeof C];

/* ================================================================== */
/*  Node type definitions — loaded from server at mount                */
/* ================================================================== */

type NodeDef = NodeTypeDef;

let _nodeDefs: NodeDef[] = [];
let _nodeDefMap: Record<string, NodeDef> = {};

function setNodeDefs(defs: NodeDef[]) {
  _nodeDefs = defs;
  _nodeDefMap = Object.fromEntries(defs.map(d => [d.type, d]));
}

function getPinDefs(nodeType: string): PinDef[] {
  return _nodeDefMap[nodeType]?.pins || [];
}

function pinTypesCompatible(srcType: string, tgtType: string): boolean {
  if (srcType === 'any' || tgtType === 'any') return true;
  return srcType === tgtType;
}

function getPinType(nodeType: string, handleId: string): string {
  const pin = getPinDefs(nodeType).find(p => p.id === handleId);
  return pin?.pin_type || 'any';
}

const PIN_TYPE_COLORS: Record<string, string> = {
  string:       '#7fba55',  // green
  message:      '#5b9bd5',  // blue
  message_list: '#5b9bd5',  // blue (composed → same as message)
  stream:       '#5cc6c6',  // cyan
  json:         '#d4a44c',  // amber
  bool:         '#e06090',  // pink
  int:          '#cc7832',  // orange
  float:        '#cc7832',  // orange (same as int)
  any:          '#888888',  // grey
};

function pinTypeColor(pinType: string | undefined): string {
  return PIN_TYPE_COLORS[pinType || 'string'] || PIN_TYPE_COLORS.string;
}

function isComposedType(pinType: string | undefined): boolean {
  if (!pinType) return false;
  return pinType.endsWith('_list') || pinType.startsWith('stream[');
}

/* ================================================================== */
/*  Custom edge components                                             */
/* ================================================================== */

function ExecEdge(props: EdgeProps) {
  const traversed = !!(props.data as any)?._traversed;
  const [path] = getBezierPath({
    sourceX: props.sourceX, sourceY: props.sourceY,
    targetX: props.targetX, targetY: props.targetY,
    sourcePosition: props.sourcePosition, targetPosition: props.targetPosition,
  });
  const stroke = props.selected ? '#ff6060' : traversed ? C.green : C.white;
  const strokeWidth = props.selected ? 3 : traversed ? 2.5 : 2;
  return (
    <>
      <BaseEdge path={path} style={{ stroke, strokeWidth, transition: 'stroke 0.3s' }} />
      {traversed && (
        <BaseEdge path={path} style={{
          stroke: C.green, strokeWidth: 4, opacity: 0.2,
          filter: `drop-shadow(0 0 3px ${C.green})`,
        }} />
      )}
    </>
  );
}

function DataEdge(props: EdgeProps) {
  const color = (props.data as any)?.color || C.green;
  const invalid = (props.data as any)?._invalid === true;
  const [path, labelX, labelY] = getBezierPath({
    sourceX: props.sourceX, sourceY: props.sourceY,
    targetX: props.targetX, targetY: props.targetY,
    sourcePosition: props.sourcePosition, targetPosition: props.targetPosition,
  });
  const stroke = props.selected ? '#ff6060' : invalid ? '#ff4444' : color;
  return (
    <>
      <BaseEdge path={path} style={{
        stroke,
        strokeWidth: props.selected ? 2.5 : invalid ? 2 : 1.5,
        strokeDasharray: invalid ? '6 3' : undefined,
        opacity: props.selected ? 1 : invalid ? 0.9 : 0.7,
      }} />
      {invalid && (
        <foreignObject
          x={labelX - 8} y={labelY - 8}
          width={16} height={16}
          style={{ pointerEvents: 'none', overflow: 'visible' }}
        >
          <div style={{
            width: 16, height: 16, borderRadius: '50%',
            background: '#ff4444', color: '#fff',
            fontSize: 11, fontWeight: 'bold',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            border: '1px solid #cc0000',
          }}>
            !
          </div>
        </foreignObject>
      )}
    </>
  );
}

const edgeTypes: EdgeTypes = {
  exec: ExecEdge,
  data: DataEdge,
};

/* ================================================================== */
/*  Shared NodeShell — two-column layout (Inputs | Outputs)            */
/* ================================================================== */

const HEADER_H = 22;
const EXEC_ROW_H = 22;
const DATA_ROW_H = 22;
const DATA_FIELD_ROW_H = 44;

interface NodeShellProps {
  def: NodeDef;
  selected?: boolean;
  connectedHandles?: Set<string>;
  data: Record<string, unknown>;
  onUpdate?: (data: Record<string, unknown>) => void;
  pinWidgets?: Record<string, React.ReactNode>;
  children?: React.ReactNode;
}

function pinDataKey(pin: PinDef): string {
  return pin.id.replace(/^data_/, '');
}

interface ComputedPin {
  pin: PinDef;
  rowH: number;
  center: number;
  cumTop: number;
}

function computeColumn(
  pins: PinDef[],
  isInput: boolean,
  connected: Set<string>,
  widgets: Record<string, React.ReactNode>,
): ComputedPin[] {
  let y = 0;
  return pins.map(pin => {
    let rowH: number;
    if (pin.kind === 'exec') {
      rowH = EXEC_ROW_H;
    } else if (isInput && !connected.has(pin.id)) {
      rowH = DATA_FIELD_ROW_H;
    } else if (widgets[pin.id]) {
      rowH = DATA_FIELD_ROW_H;
    } else {
      rowH = DATA_ROW_H;
    }
    const center = y + (pin.kind === 'exec' ? rowH / 2 : 11);
    const cumTop = y;
    y += rowH;
    return { pin, rowH, center, cumTop };
  });
}

const CHAR_W = 5.8;
const COL_PAD = 20;
const MIN_FIELD_W = 56;
const EXEC_COL_W = 10;

function colWidth(pins: PinDef[], hasField: boolean): number {
  const maxLabel = pins.reduce((m, p) => Math.max(m, p.label.length), 0);
  const labelW = maxLabel * CHAR_W + COL_PAD;
  return Math.max(labelW, hasField ? MIN_FIELD_W : EXEC_COL_W);
}

const fieldBaseStyle: CSSProperties = {
  width: '100%', fontSize: 10, padding: '2px 4px', marginTop: 2,
  background: '#222', border: `1px solid ${C.border}`, borderRadius: 2,
  color: C.text, outline: 'none', height: 20, boxSizing: 'border-box',
};

const selectStyle: CSSProperties = {
  ...fieldBaseStyle, cursor: 'pointer', appearance: 'auto',
};

type DropdownOption = { value: string; label: string };

const _dynamicFetchers: Record<string, () => Promise<DropdownOption[]>> = {
  agents: () => listAgents().then(list =>
    list.map(a => ({ value: a.name, label: a.name })),
  ),
  conversations: () => listConversations().then(list =>
    list.map(c => ({ value: c.id, label: c.title || c.id })),
  ),
};

function useDynamicOptions(source: string | undefined): DropdownOption[] {
  const [options, setOptions] = useState<DropdownOption[]>([]);
  useEffect(() => {
    if (!source || !_dynamicFetchers[source]) return;
    _dynamicFetchers[source]().then(setOptions).catch(() => {});
  }, [source]);
  return options;
}

function PinFieldWidget({ pin, dataKey, data, onUpdate }: {
  pin: PinDef; dataKey: string;
  data: Record<string, unknown>;
  onUpdate?: (data: Record<string, unknown>) => void;
}) {
  const value = (data as any)[dataKey];
  const dynamicOpts = useDynamicOptions(pin.dynamic_choices);

  if (pin.choices && pin.choices.length > 0) {
    return (
      <select
        style={selectStyle}
        value={String(value ?? pin.choices[0] ?? '')}
        onChange={e => onUpdate?.({ ...data, [dataKey]: e.target.value })}
      >
        {pin.choices.map(c => (
          <option key={c} value={c}>{c}</option>
        ))}
      </select>
    );
  }

  if (pin.dynamic_choices && dynamicOpts.length > 0) {
    return (
      <select
        style={selectStyle}
        value={String(value ?? '')}
        onChange={e => onUpdate?.({ ...data, [dataKey]: e.target.value })}
      >
        <option value="">— select —</option>
        {dynamicOpts.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    );
  }

  if (pin.pin_type === 'bool') {
    const checked = value === true || value === 'true';
    return (
      <label style={{
        display: 'flex', alignItems: 'center', gap: 4, marginTop: 2,
        cursor: 'pointer', fontSize: 10, color: C.text,
      }}>
        <input
          type="checkbox"
          checked={checked}
          onChange={e => onUpdate?.({ ...data, [dataKey]: e.target.checked })}
          style={{ accentColor: pin.color, width: 12, height: 12, cursor: 'pointer' }}
        />
        {checked ? 'true' : 'false'}
      </label>
    );
  }

  return (
    <input
      style={fieldBaseStyle}
      value={String(value ?? '')}
      onChange={e => onUpdate?.({ ...data, [dataKey]: e.target.value })}
      placeholder={dataKey}
    />
  );
}

function PinLabel({ pin }: { pin: PinDef }) {
  const isOptional = pin.optional !== false;
  const color = pin.kind === 'data' ? pinTypeColor(pin.pin_type) : pin.color;
  const hasType = pin.kind === 'data' && pin.pin_type;
  return (
    <div className="pin-label-row" style={{
      fontSize: 10, lineHeight: '14px',
      color: isOptional ? color + '99' : color,
      position: 'relative',
    }}>
      <span className="pin-label-name" style={{ transition: 'opacity 0.15s' }}>
        {pin.label}
        {!isOptional && <span style={{ color: C.red, marginLeft: 1, fontSize: 8 }}>*</span>}
      </span>
      {hasType && (
        <span className="pin-type-tooltip" style={{
          position: 'absolute', left: 0, right: 0, top: 0,
          color: color + 'aa', fontSize: 8,
          opacity: 0, transition: 'opacity 0.15s',
          whiteSpace: 'nowrap',
        }}>{pin.pin_type}</span>
      )}
    </div>
  );
}

function heatColor(t: number, max: number): string {
  if (max <= 0) return C.green;
  const r = Math.min(t / max, 1);
  if (r < 0.25) return '#4caf50';
  if (r < 0.50) return '#8bc34a';
  if (r < 0.75) return '#ff9800';
  return '#f44336';
}

function fmtMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function NodeShell({ def, selected, connectedHandles, data, onUpdate, pinWidgets, children }: NodeShellProps) {
  if (!def) return null;
  const connected = connectedHandles || new Set<string>();
  const widgets = pinWidgets || {};
  const execActive = !!data._execActive;
  const execDone = !!data._execDone;
  const execUnreachable = !!data._execUnreachable;
  const timingMs = data._timingMs as number | null;
  const maxTimingMs = (data._maxTimingMs as number) || 0;

  const leftPins = def.pins.filter(p => p.side === 'left');
  const rightPins = def.pins.filter(p => p.side === 'right');
  const leftCol = computeColumn(leftPins, true, connected, widgets);
  const rightCol = computeColumn(rightPins, false, connected, widgets);
  const leftH = leftCol.reduce((s, c) => s + c.rowH, 0);
  const rightH = rightCol.reduce((s, c) => s + c.rowH, 0);
  const bodyH = Math.max(leftH, rightH, 22);

  const hasLeft = leftPins.length > 0;
  const hasRight = rightPins.length > 0;
  const hasBothCols = hasLeft && hasRight;

  const leftHasField = leftPins.some(p => p.kind === 'data' && !connected.has(p.id));
  const rightHasWidget = rightPins.some(p => !!widgets[p.id]);
  const leftW = hasLeft ? colWidth(leftPins, leftHasField) : 0;
  const rightW = hasRight ? colWidth(rightPins, rightHasWidget) : 0;
  const headerW = def.label.length * 6.5 + 20;
  const totalW = Math.max(leftW + rightW + (hasBothCols ? 1 : 0), headerW);

  let borderColor = selected ? def.accent : C.border;
  let boxShadow: string | undefined;
  if (execActive) {
    borderColor = C.green;
    boxShadow = `0 0 8px ${C.green}88, 0 0 2px ${C.green}`;
  } else if (execDone) {
    borderColor = C.green + '99';
  }

  const nodeStyle: CSSProperties = {
    background: C.node,
    borderRadius: 4,
    border: `2px solid ${borderColor}`,
    width: totalW,
    fontSize: 11,
    overflow: 'visible',
    boxShadow,
    transition: 'border-color 0.2s, box-shadow 0.2s, opacity 0.3s',
    position: 'relative',
    opacity: execUnreachable ? 0.35 : 1,
  };

  const headerBg = execActive ? C.green + '30' : execDone ? C.green + '18' : def.accent + '26';

  return (
    <div style={nodeStyle}>
      {/* Header */}
      <div style={{
        height: HEADER_H, display: 'flex', alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 8px', background: headerBg,
        borderRadius: '3px 3px 0 0',
        borderBottom: `1px solid ${C.border}`,
      }}>
        <span style={{ color: C.text, fontWeight: 500, fontSize: 11, letterSpacing: '0.02em' }}>
          {def.label}
        </span>
        {timingMs != null && (
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 2,
            fontSize: 9, fontWeight: 500, lineHeight: 1,
            color: heatColor(timingMs, maxTimingMs),
          }}>
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            {fmtMs(timingMs)}
          </span>
        )}
      </div>

      {/* Two-column body */}
      <div style={{ display: 'flex', position: 'relative', minHeight: bodyH }}>
        {/* Left column (inputs) */}
        {hasLeft && (
          <div style={{
            width: leftW,
            ...(hasBothCols ? { borderRight: `1px solid ${C.border}` } : {}),
          }}>
            {leftCol.map(({ pin, rowH }) => {
              const key = pinDataKey(pin);
              const showField = pin.kind === 'data' && !connected.has(pin.id);
              return (
                <div key={pin.id} style={{ height: rowH, padding: pin.kind === 'exec' ? '0 4px 0 12px' : '3px 4px 3px 12px', display: 'flex', flexDirection: 'column', justifyContent: pin.kind === 'exec' ? 'center' : 'flex-start' }}>
                  {pin.label && <PinLabel pin={pin} />}
                  {showField && (
                    <PinFieldWidget pin={pin} dataKey={key} data={data} onUpdate={onUpdate} />
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Right column (outputs) */}
        {hasRight && (
          <div style={{ width: rightW }}>
            {rightCol.map(({ pin, rowH }) => (
              <div key={pin.id} style={{ height: rowH, padding: pin.kind === 'exec' ? '0 12px 0 4px' : '3px 12px 3px 4px', display: 'flex', flexDirection: 'column', justifyContent: pin.kind === 'exec' ? 'center' : 'flex-start', alignItems: 'flex-end' }}>
                {pin.label && <PinLabel pin={pin} />}
                {widgets[pin.id] && (
                  <div style={{ width: '100%', marginTop: 2 }}>{widgets[pin.id]}</div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Handles + pin shapes (absolute, at node edges) */}
        {leftCol.map(({ pin, center }) => (
          <PinHandle key={pin.id} pin={pin} side="left" top={center} isConnected={connected.has(pin.id)} />
        ))}
        {rightCol.map(({ pin, center }) => (
          <PinHandle key={pin.id} pin={pin} side="right" top={center} isConnected={connected.has(pin.id)} />
        ))}
      </div>

      {/* Optional extra body content */}
      {children && (
        <div style={{ padding: '4px 8px 6px', borderTop: `1px solid ${C.border}` }}>
          {children}
        </div>
      )}
    </div>
  );
}

/* ================================================================== */
/*  PinHandle — handle + visual shape at node edge                     */
/* ================================================================== */

function PinHandle({ pin, side, top, isConnected }: {
  pin: PinDef; side: 'left' | 'right'; top: number; isConnected: boolean;
}) {
  const isExec = pin.kind === 'exec';
  const position = side === 'left' ? Position.Left : Position.Right;
  const handleType = side === 'left' ? 'target' : 'source';

  const isRequired = !isExec && pin.optional === false;
  const color = isExec ? pin.color : pinTypeColor(pin.pin_type);
  const composed = !isExec && isComposedType(pin.pin_type);

  const shapeStyle: CSSProperties = isExec ? {
    width: 0, height: 0,
    borderTop: '5px solid transparent',
    borderBottom: '5px solid transparent',
    borderLeft: `7px solid ${isConnected ? color : 'transparent'}`,
    borderRight: 'none',
    background: 'transparent',
    borderRadius: 0,
  } : {
    width: 8, height: 8,
    borderRadius: composed ? 2 : '50%',
    background: isConnected ? color : 'transparent',
    border: `1.5px ${isRequired && !isConnected ? 'dashed' : 'solid'} ${color}`,
  };

  const execStroke: CSSProperties | null = isExec && !isConnected ? {
    width: 0, height: 0,
    borderTop: '5px solid transparent',
    borderBottom: '5px solid transparent',
    borderLeft: `7px solid ${color}`,
    borderRight: 'none',
    background: 'transparent',
    borderRadius: 0,
    opacity: 0.35,
  } : null;

  return (
    <>
      <Handle
        type={handleType}
        position={position}
        id={pin.id}
        style={{
          top,
          width: isExec ? 10 : 8,
          height: isExec ? 10 : 8,
          background: 'transparent',
          border: 'none',
          zIndex: 10,
        }}
      />
      <div style={{
        position: 'absolute',
        top: top - (isExec ? 5 : 4),
        ...(side === 'left' ? { left: -5 } : { right: -5 }),
        pointerEvents: 'none',
        ...shapeStyle,
      }} />
      {execStroke && (
        <div style={{
          position: 'absolute',
          top: top - 5,
          ...(side === 'left' ? { left: -5 } : { right: -5 }),
          pointerEvents: 'none',
          ...execStroke,
        }} />
      )}
    </>
  );
}

/* ================================================================== */
/*  Node components                                                    */
/* ================================================================== */

type NodeDataUpdater = ((d: Record<string, unknown>) => void) | undefined;

const outputFieldStyle: CSSProperties = {
  width: '100%', fontSize: 10, padding: '2px 4px', height: 20,
  background: '#222', border: `1px solid ${C.border}`, borderRadius: 2,
  color: C.text, outline: 'none', boxSizing: 'border-box',
};

function nodeProps(data: Record<string, unknown>) {
  return {
    data,
    connectedHandles: data._connected as Set<string>,
    onUpdate: data._onUpdate as NodeDataUpdater,
  };
}

function StartNode({ data, selected }: NodeProps) {
  const onUpdate = data._onUpdate as NodeDataUpdater;
  const pinWidgets: Record<string, React.ReactNode> = {
    data_message: (
      <input style={outputFieldStyle}
        value={String(data.preview_message || '')}
        onChange={e => onUpdate?.({ ...data, preview_message: e.target.value })}
        placeholder="Hello!" />
    ),
  };
  return (
    <NodeShell def={_nodeDefMap['start']} selected={selected}
      {...nodeProps(data)} pinWidgets={pinWidgets} />
  );
}

function FetchConversationNode({ data, selected }: NodeProps) {
  return (
    <NodeShell def={_nodeDefMap['fetch_conversation']} selected={selected}
      {...nodeProps(data)} />
  );
}

function AgentNodeComponent(typeName: string) {
  const Comp: FC<NodeProps> = ({ data, selected }) => {
    const baseDef = _nodeDefMap[typeName];
    const [templateInputs, setTemplateInputs] = useState<string[]>([]);
    const agentName = String(data.agent || '');

    useEffect(() => {
      if (!agentName) { setTemplateInputs([]); return; }
      getAgentInputs(agentName).then(setTemplateInputs).catch(() => setTemplateInputs([]));
    }, [agentName]);

    if (!baseDef) return null;

    const mergedDef = useMemo(() => {
      if (templateInputs.length === 0) return baseDef;
      const extraPins: PinDef[] = templateInputs.map(name => ({
        id: `data_${name}`,
        label: name,
        color: C.cyan,
        side: 'left' as const,
        kind: 'data' as const,
        pin_type: 'string',
        optional: true,
      }));
      return { ...baseDef, pins: [...baseDef.pins, ...extraPins] };
    }, [baseDef, templateInputs]);

    return (
      <NodeShell def={mergedDef} selected={selected} {...nodeProps(data)} />
    );
  };
  Comp.displayName = typeName;
  return Comp;
}

function GenericNode(typeName: string) {
  const Comp: FC<NodeProps> = ({ data, selected }) => {
    const def = _nodeDefMap[typeName];
    if (!def) return null;
    return <NodeShell def={def} selected={selected} {...nodeProps(data)} />;
  };
  Comp.displayName = typeName;
  return Comp;
}

function buildNodeTypes(): NodeTypes {
  const types: NodeTypes = {
    start: StartNode,
    fetch_conversation: FetchConversationNode,
    agent: AgentNodeComponent('agent'),
    agent_call: AgentNodeComponent('agent_call'),
  };
  for (const def of _nodeDefs) {
    if (types[def.type]) continue;
    types[def.type] = GenericNode(def.type);
  }
  return types;
}

/* ================================================================== */
/*  SVG icons                                                          */
/* ================================================================== */

const PlayIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
);
const SaveIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" />
    <polyline points="17 21 17 13 7 13 7 21" /><polyline points="7 3 7 8 15 8" />
  </svg>
);
const TrashIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" />
    <path d="M10 11v6M14 11v6" />
  </svg>
);
const LockIcon = () => (
  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="11" width="18" height="11" rx="2" />
    <path d="M7 11V7a5 5 0 0110 0v4" />
  </svg>
);
const UnlockIcon = () => (
  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="11" width="18" height="11" rx="2" />
    <path d="M7 11V7a5 5 0 018-3" />
  </svg>
);
const ChatIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
  </svg>
);
/* ================================================================== */
/*  Context menu — right-click to add nodes                            */
/* ================================================================== */

interface ContextMenuProps {
  x: number;
  y: number;
  flowX: number;
  flowY: number;
  onAddNode: (type: string, position: { x: number; y: number }) => void;
  onClose: () => void;
}

const CATEGORY_ORDER = ['Flow', 'Agent', 'Data', 'Debug', 'General'];

function ContextMenu({ x, y, flowX, flowY, onAddNode, onClose }: ContextMenuProps) {
  const [openCat, setOpenCat] = useState<string | null>(null);

  useEffect(() => {
    const handle = () => onClose();
    window.addEventListener('click', handle);
    return () => window.removeEventListener('click', handle);
  }, [onClose]);

  const grouped = useMemo(() => {
    const map: Record<string, typeof _nodeDefs> = {};
    for (const def of _nodeDefs) {
      const cat = def.category || 'General';
      (map[cat] ||= []).push(def);
    }
    return CATEGORY_ORDER.filter(c => map[c]).map(c => ({ category: c, defs: map[c] }));
  }, []);

  const menuH = grouped.length * 32 + 32;
  const fitsBelow = y + menuH < window.innerHeight - 8;
  const adjustedY = fitsBelow ? y : Math.max(8, window.innerHeight - menuH - 8);

  return (
    <div style={{
      position: 'fixed', left: x, top: adjustedY, zIndex: 1000,
      background: '#2a2a2a', border: `1px solid ${C.border}`, borderRadius: 4,
      minWidth: 150, padding: '4px 0', boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
    }} onClick={e => e.stopPropagation()}>
      <div style={{ fontSize: 11, color: C.muted, padding: '4px 12px', textTransform: 'uppercase', fontWeight: 600 }}>
        Add Node
      </div>
      {grouped.map(({ category, defs }) => (
        <div key={category} style={{ position: 'relative' }}
          onMouseEnter={() => setOpenCat(category)}
          onMouseLeave={() => setOpenCat(null)}
        >
          <div style={{
            padding: '6px 12px', cursor: 'pointer', display: 'flex',
            alignItems: 'center', justifyContent: 'space-between',
            fontSize: 14, color: C.text, fontWeight: 500,
            background: openCat === category ? '#383838' : 'transparent',
          }}>
            <span>{category}</span>
            <span style={{ fontSize: 10, color: C.muted }}>&#9654;</span>
          </div>
          {openCat === category && (
            <div style={{
              position: 'absolute', left: '100%', top: 0, zIndex: 1001,
              background: '#2a2a2a', border: `1px solid ${C.border}`, borderRadius: 4,
              minWidth: 180, padding: '4px 0', boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
            }}>
              {defs.map(def => (
                <div key={def.type}
                  onClick={() => { onAddNode(def.type, { x: flowX, y: flowY }); onClose(); }}
                  style={{
                    padding: '5px 12px', cursor: 'pointer', display: 'flex',
                    alignItems: 'center', gap: 8, fontSize: 14, color: C.text,
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#383838')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <div style={{ width: 6, height: 6, borderRadius: 1, background: def.accent, flexShrink: 0 }} />
                  <div>
                    <div style={{ fontWeight: 500 }}>{def.label}</div>
                    <div style={{ fontSize: 12, color: C.muted }}>{def.description}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/* ================================================================== */
/*  Property panel                                                     */
/* ================================================================== */

interface PropPanelProps {
  node: Node;
  onUpdate: (id: string, data: Record<string, unknown>) => void;
  onDelete: (id: string) => void;
}

const PropPanel: FC<PropPanelProps> = ({ node, onUpdate, onDelete }) => {
  const data = node.data || {};
  const ntype = node.type || '';

  const field = (label: string, key: string, opts?: { placeholder?: string; mono?: boolean; rows?: number }) => (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 14, color: C.muted, marginBottom: 2 }}>{label}</div>
      {(opts?.rows || 0) > 1 ? (
        <Textarea
          size="sm" rows={opts!.rows}
          value={String((data as any)[key] || '')}
          placeholder={opts?.placeholder}
          onChange={e => onUpdate(node.id, { ...data, [key]: e.target.value })}
          bg="#222" borderColor={C.border} fontSize="xs"
          fontFamily={opts?.mono ? 'monospace' : undefined}
        />
      ) : (
        <Input
          size="sm"
          value={String((data as any)[key] || '')}
          placeholder={opts?.placeholder}
          onChange={e => onUpdate(node.id, { ...data, [key]: e.target.value })}
          bg="#222" borderColor={C.border} fontSize="xs"
          fontFamily={opts?.mono ? 'monospace' : undefined}
        />
      )}
    </div>
  );

  return (
    <VStack align="stretch" gap={2} p={3}>
      <HStack justify="space-between">
        <Text fontWeight="bold" fontSize="xs" color={C.text}>Properties</Text>
        <IconButton aria-label="Delete" size="xs" variant="ghost" colorScheme="red"
          onClick={() => onDelete(node.id)}><TrashIcon /></IconButton>
      </HStack>
      <Text fontSize="14px" color={C.muted}>{ntype} node</Text>

      {field('Label', 'label', { placeholder: ntype })}

      {ntype === 'start' && (
        <>
          {field('Preview Message', 'preview_message', { placeholder: 'Test user message', rows: 3 })}
        </>
      )}
      {ntype === 'agent' && (
        <>
          {field('Agent', 'agent', { placeholder: 'default' })}
          {field('Prompt Template', 'prompt_template', { placeholder: '{{context}}', rows: 4 })}
        </>
      )}
      {ntype === 'tool' && (
        <>
          {field('Tool Name', 'tool', { placeholder: 'e.g. web.search' })}
          {field('Args (JSON)', 'args_json', { placeholder: '{"query": "{{input}}"}', rows: 3, mono: true })}
        </>
      )}
      {ntype === 'forward' && (
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 14, color: C.muted, marginBottom: 2 }}>Mode</div>
          <HStack gap={1}>
            {['token', 'reasoning'].map(mode => (
              <Button key={mode} size="xs" variant="outline" flex={1}
                bg={(data as any).mode === mode || (!((data as any).mode) && mode === 'token') ? (mode === 'reasoning' ? C.purple + '33' : C.green + '33') : 'transparent'}
                borderColor={(data as any).mode === mode || (!((data as any).mode) && mode === 'token') ? (mode === 'reasoning' ? C.purple : C.green) : C.border}
                color={(data as any).mode === mode || (!((data as any).mode) && mode === 'token') ? (mode === 'reasoning' ? C.purple : C.green) : C.muted}
                onClick={() => onUpdate(node.id, { ...data, mode })}
                _hover={{ bg: '#333' }} fontSize="14px"
              >{mode}</Button>
            ))}
          </HStack>
          <Text fontSize="13px" color={C.muted} mt={1}>
            token = visible reply, reasoning = thinking bubble
          </Text>
        </div>
      )}
      {ntype === 'condition' && (
        <>
          {field('Expression', 'expression', { placeholder: 'len(input) > 100', mono: true })}
          <Text fontSize="13px" color={C.muted}>Variables: input, len</Text>
        </>
      )}
    </VStack>
  );
};

/* ================================================================== */
/*  Connection validation                                              */
/* ================================================================== */

function isValidConnection(conn: Connection | Edge): boolean {
  const src = conn.sourceHandle || '';
  const tgt = conn.targetHandle || '';
  const srcIsExec = src.startsWith('exec_');
  const tgtIsExec = tgt.startsWith('exec_');
  return srcIsExec === tgtIsExec;
}

function edgeTypeFromHandles(sourceHandle: string | null): 'exec' | 'data' {
  return (sourceHandle || '').startsWith('exec_') ? 'exec' : 'data';
}

function edgeColorFromHandle(sourceHandle: string | null, nodeType: string | undefined): string {
  if ((sourceHandle || '').startsWith('exec_')) return C.white;
  const pins = getPinDefs(nodeType || '');
  const pin = pins.find(p => p.id === sourceHandle);
  if (pin && pin.kind === 'data') return pinTypeColor(pin.pin_type);
  return pin?.color || C.green;
}

/* ================================================================== */
/*  Defaults                                                           */
/* ================================================================== */

let _ctr = 0;
const nid = () => `n_${++_ctr}_${Date.now().toString(36)}`;

const DEFAULT_NODES: Node[] = [
  { id: 'start',  type: 'start',  position: { x: 50,  y: 150 }, data: { label: 'Start', preview_message: 'Hello!' } },
  { id: 'agent1', type: 'agent',  position: { x: 350, y: 150 }, data: { agent: 'default', label: 'Agent' } },
  { id: 'end',    type: 'output', position: { x: 650, y: 150 }, data: { label: 'Done' } },
];

const DEFAULT_EDGES: Edge[] = [
  { id: 'e1', source: 'start',  target: 'agent1', sourceHandle: 'exec_out',      targetHandle: 'exec_in',      type: 'exec' },
  { id: 'e2', source: 'agent1', target: 'end',    sourceHandle: 'exec_out',      targetHandle: 'exec_in',      type: 'exec' },
  { id: 'e3', source: 'start',  target: 'agent1', sourceHandle: 'data_message',  targetHandle: 'data_context', type: 'data', data: { color: C.amber } },
  { id: 'e4', source: 'agent1', target: 'end',    sourceHandle: 'data_response', targetHandle: 'data_response', type: 'data', data: { color: C.green } },
];

/* ================================================================== */
/*  SSE parser helper                                                  */
/* ================================================================== */

/* ================================================================== */
/*  Main editor                                                        */
/* ================================================================== */

const WorkflowEditor: FC = () => {
  const { workflowIdParam } = useParams<{ workflowIdParam?: string }>();
  const navigate = useNavigate();

  const [nodes, setNodes, onNodesChange] = useNodesState(DEFAULT_NODES);
  const [edges, setEdges, onEdgesChange] = useEdgesState(DEFAULT_EDGES);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);
  const pendingCenter = useRef<{ x: number; y: number } | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const [workflowId, _setWorkflowId] = useState('');
  const [workflowName, setWorkflowName] = useState('New Workflow');
  const [workflowDesc, setWorkflowDesc] = useState('');

  const setWorkflowId = useCallback((id: string) => {
    _setWorkflowId(id);
    if (id) {
      navigate(`/workflows/${id}`, { replace: true });
    } else {
      navigate('/workflows', { replace: true });
    }
  }, [navigate]);
  const [savedWorkflows, setSavedWorkflows] = useState<WorkflowSummary[]>([]);
  const [showSidebar, setShowSidebar] = useState(true);
  const [devMode, setDevMode] = useState(false);
  const [isBuiltin, setIsBuiltin] = useState(false);

  /* Context menu */
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; flowX: number; flowY: number } | null>(null);

  /* Chat panel */
  const [showChat, setShowChat] = useState(false);
  const [testChatKey, setTestChatKey] = useState(0);
  const [runLog, setRunLog] = useState<string[]>([]);
  const [expandedCat, setExpandedCat] = useState<string | null>('Agent');
  const [nodeDefsVersion, setNodeDefsVersion] = useState(0);

  /* Execution highlighting state */
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);
  const [completedNodeIds, setCompletedNodeIds] = useState<Set<string>>(new Set());
  const [activeEdgeId, setActiveEdgeId] = useState<string | null>(null);
  const [lastAudit, setLastAudit] = useState<any>(null);

  /* Validation */
  const [validationErrors, setValidationErrors] = useState<ValidationError[]>([]);
  const [showValidation, setShowValidation] = useState(false);

  const handleRfInit = useCallback((instance: ReactFlowInstance) => {
    setRfInstance(instance);
    if (pendingCenter.current) {
      const { x, y } = pendingCenter.current;
      pendingCenter.current = null;
      setTimeout(() => instance.setCenter(x, y, { zoom: 1 }), 50);
    } else {
      instance.fitView();
    }
  }, []);

  useEffect(() => {
    getNodeTypes().then(defs => {
      setNodeDefs(defs);
      setNodeDefsVersion(v => v + 1);
    }).catch(() => {});
    listWorkflows().then(setSavedWorkflows).catch(() => {});
  }, []);

  const nodeTypes = useMemo(() => buildNodeTypes(), [nodeDefsVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Compute connected handles set per node for hollow/filled pins */
  const connectedMap = useMemo(() => {
    const map: Record<string, Set<string>> = {};
    for (const e of edges) {
      if (!map[e.source]) map[e.source] = new Set();
      if (!map[e.target]) map[e.target] = new Set();
      if (e.sourceHandle) map[e.source].add(e.sourceHandle);
      if (e.targetHandle) map[e.target].add(e.targetHandle);
    }
    return map;
  }, [edges]);

  const updateNodeData = useCallback(
    (id: string, data: Record<string, unknown>) => {
      setNodes(nds => nds.map(n => (n.id === id ? { ...n, data } : n)));
      setSelectedNode(prev => (prev && prev.id === id ? { ...prev, data } : prev));
    },
    [setNodes],
  );

  const nodeTimings = useMemo(() => {
    const map: Record<string, number> = {};
    if (!lastAudit?.events) return map;
    for (const e of lastAudit.events) {
      if (e.event === 'node.end' && e.node_id && e.duration_ms != null) {
        map[e.node_id] = e.duration_ms;
      } else if (e.event === 'node.exec' && e.node_id && e.duration_ms != null) {
        map[e.node_id] = e.duration_ms;
      }
    }
    return map;
  }, [lastAudit]);

  const maxNodeTime = useMemo(() => {
    const vals = Object.values(nodeTimings);
    return vals.length > 0 ? Math.max(...vals) : 0;
  }, [nodeTimings]);

  const execReachable = useMemo(() => {
    const reachable = new Set<string>();
    const queue: string[] = [];
    const startNodes = nodes.filter(n => n.type === 'start');
    for (const s of startNodes) { reachable.add(s.id); queue.push(s.id); }
    while (queue.length > 0) {
      const cur = queue.shift()!;
      for (const e of edges) {
        if (e.type === 'exec' && e.source === cur && !reachable.has(e.target)) {
          reachable.add(e.target);
          queue.push(e.target);
        }
      }
    }
    return reachable;
  }, [nodes, edges]);

  const enrichedNodes = useMemo(() =>
    nodes.map(n => ({
      ...n,
      data: {
        ...n.data,
        _connected: connectedMap[n.id] || new Set(),
        _onUpdate: (d: Record<string, unknown>) => updateNodeData(n.id, d),
        _execActive: n.id === activeNodeId,
        _execDone: completedNodeIds.has(n.id),
        _timingMs: nodeTimings[n.id] ?? null,
        _maxTimingMs: maxNodeTime,
        _execUnreachable: !execReachable.has(n.id),
      },
    })),
  [nodes, connectedMap, updateNodeData, activeNodeId, completedNodeIds, nodeTimings, maxNodeTime, execReachable]);

  const enrichedEdges = useMemo(() =>
    edges.map(e => {
      let _invalid = false;
      if (e.type === 'data' && e.sourceHandle && e.targetHandle) {
        const srcNode = nodes.find(n => n.id === e.source);
        const tgtNode = nodes.find(n => n.id === e.target);
        if (srcNode && tgtNode) {
          const srcType = getPinType(srcNode.type || '', e.sourceHandle);
          const tgtType = getPinType(tgtNode.type || '', e.targetHandle);
          _invalid = !pinTypesCompatible(srcType, tgtType);
        }
      }
      return {
        ...e,
        data: { ...(e.data || {}), _traversed: e.id === activeEdgeId, _invalid },
      };
    }),
  [edges, nodes, activeEdgeId]);

  const onConnect = useCallback((params: Connection) => {
    const etype = edgeTypeFromHandles(params.sourceHandle);
    const color = edgeColorFromHandle(params.sourceHandle, nodes.find(n => n.id === params.source)?.type);
    setEdges(eds => addEdge({
      ...params,
      type: etype,
      ...(etype === 'data' ? { data: { color } } : {}),
    }, eds));
  }, [setEdges, nodes]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => setSelectedNode(node), []);
  const onPaneClick = useCallback(() => { setSelectedNode(null); setCtxMenu(null); }, []);

  const deleteNode = useCallback((id: string) => {
    setNodes(nds => nds.filter(n => n.id !== id));
    setEdges(eds => eds.filter(e => e.source !== id && e.target !== id));
    setSelectedNode(null);
  }, [setNodes, setEdges]);

  /* Add node (used by both drag-drop and context menu) */
  const addNode = useCallback((type: string, position: { x: number; y: number }) => {
    const defaultData: Record<string, unknown> = { label: _nodeDefMap[type]?.label || type };
    if (type === 'agent' || type === 'agent_call') defaultData.agent = 'default';
    if (type === 'condition') defaultData.expression = 'True';
    if (type === 'start') { defaultData.preview_message = 'Hello!'; }
    if (type === 'fetch_conversation') { defaultData.conversation_id = ''; defaultData.debug = true; }
    setNodes(nds => [...nds, { id: nid(), type, position, data: defaultData }]);
  }, [setNodes]);

  /* Drag & drop */
  const onDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const type = event.dataTransfer.getData('application/workflow-node-type');
    if (!type || !rfInstance || !wrapperRef.current) return;
    const bounds = wrapperRef.current.getBoundingClientRect();
    const position = rfInstance.screenToFlowPosition({
      x: event.clientX - bounds.left, y: event.clientY - bounds.top,
    });
    addNode(type, position);
  }, [rfInstance, addNode]);

  /* Right-click context menu */
  const onPaneContextMenu = useCallback((event: MouseEvent | React.MouseEvent) => {
    event.preventDefault();
    if (!rfInstance) return;
    const flowPos = rfInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    setCtxMenu({ x: event.clientX, y: event.clientY, flowX: flowPos.x, flowY: flowPos.y });
  }, [rfInstance]);

  /* Build spec */
  const buildSpec = useCallback((): WorkflowSpec => ({
    id: workflowId || workflowName.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, ''),
    name: workflowName,
    description: workflowDesc,
    nodes: nodes.map(n => ({
      id: n.id, type: n.type || 'agent', position: n.position,
      data: Object.fromEntries(Object.entries(n.data || {}).filter(([k]) => !k.startsWith('_'))),
    })),
    edges: edges.map(e => ({
      id: e.id, source: e.source, target: e.target,
      sourceHandle: e.sourceHandle || '', targetHandle: e.targetHandle || '',
      type: (e.type as string) || 'data',
    })),
  }), [nodes, edges, workflowId, workflowName, workflowDesc]);

  /* Save */
  const handleSave = useCallback(async () => {
    const spec = buildSpec();
    try {
      if (devMode && isBuiltin) {
        await saveBuiltinWorkflow(spec.id, spec);
      } else if (workflowId && !savedWorkflows.find(w => w.id === workflowId && (w as any).builtin)) {
        await updateWorkflow(spec.id, spec);
      } else {
        await saveWorkflow(spec);
      }
      setWorkflowId(spec.id);
      setSavedWorkflows(await listWorkflows());
    } catch (err) { console.error('Save failed', err); }
  }, [buildSpec, workflowId, savedWorkflows, devMode, isBuiltin]);

  /* Validate */
  const handleValidate = useCallback(async () => {
    if (workflowId) {
      try {
        await handleSave();
        const result = await validateWorkflow(workflowId);
        setValidationErrors(result.errors);
        setShowValidation(true);
      } catch (err) { console.error('Validate failed', err); }
    } else {
      const clientErrors: ValidationError[] = [];
      for (const e of edges) {
        if (e.type !== 'data' || !e.sourceHandle || !e.targetHandle) continue;
        const srcNode = nodes.find(n => n.id === e.source);
        const tgtNode = nodes.find(n => n.id === e.target);
        if (!srcNode || !tgtNode) continue;
        const srcType = getPinType(srcNode.type || '', e.sourceHandle);
        const tgtType = getPinType(tgtNode.type || '', e.targetHandle);
        if (!pinTypesCompatible(srcType, tgtType)) {
          const srcLabel = (srcNode.data as any)?.label || srcNode.id;
          const tgtLabel = (tgtNode.data as any)?.label || tgtNode.id;
          const srcPin = getPinDefs(srcNode.type || '').find(p => p.id === e.sourceHandle);
          const tgtPin = getPinDefs(tgtNode.type || '').find(p => p.id === e.targetHandle);
          clientErrors.push({
            edge_id: e.id,
            source_node: srcNode.id,
            target_node: tgtNode.id,
            source_pin: srcPin?.label || e.sourceHandle,
            target_pin: tgtPin?.label || e.targetHandle,
            source_type: srcType,
            target_type: tgtType,
            message: `${srcLabel}.${srcPin?.label || '?'} (${srcType}) \u2192 ${tgtLabel}.${tgtPin?.label || '?'} (${tgtType}): incompatible types`,
          });
        }
      }
      setValidationErrors(clientErrors);
      setShowValidation(true);
    }
  }, [workflowId, edges, nodes, handleSave]);

  /* Load */
  const loadSpec = useCallback((spec: any) => {
    setWorkflowId(spec.id || '');
    setWorkflowName(spec.name || spec.id || '');
    setWorkflowDesc(spec.description || '');
    setIsBuiltin(!!(spec as any).builtin);
    const specNodes = (spec.nodes || []).map((n: any) => ({
      id: n.id, type: n.type || 'agent',
      position: n.position || { x: 100, y: 100 }, data: n.data || {},
    }));
    setNodes(specNodes);
    setEdges((spec.edges || []).map((e: any) => {
      const srcNode = specNodes.find((n: any) => n.id === e.source);
      const color = edgeColorFromHandle(e.sourceHandle, srcNode?.type);
      return {
        id: e.id, source: e.source, target: e.target,
        sourceHandle: e.sourceHandle || '', targetHandle: e.targetHandle || '',
        type: e.type || 'data',
        ...(e.type === 'data' ? { data: { color } } : {}),
      };
    }));
    setSelectedNode(null);

    if (specNodes.length > 0) {
      const cx = specNodes.reduce((s: number, n: any) => s + n.position.x, 0) / specNodes.length;
      const cy = specNodes.reduce((s: number, n: any) => s + n.position.y, 0) / specNodes.length;
      if (rfInstance) {
        setTimeout(() => rfInstance.setCenter(cx, cy, { zoom: 1 }), 50);
      } else {
        pendingCenter.current = { x: cx, y: cy };
      }
    }
  }, [setNodes, setEdges, setWorkflowId, rfInstance]);

  const handleLoad = useCallback(async (id: string) => {
    try { loadSpec(await getWorkflow(id)); } catch (err) { console.error('Load failed', err); }
  }, [loadSpec]);

  /* Auto-load workflow from URL param */
  const initialLoadDone = useRef(false);
  useEffect(() => {
    if (initialLoadDone.current) return;
    if (workflowIdParam) {
      initialLoadDone.current = true;
      handleLoad(workflowIdParam);
    }
  }, [workflowIdParam, handleLoad]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deleteWorkflow(id);
      setSavedWorkflows(await listWorkflows());
      if (workflowId === id) {
        setWorkflowId(''); setWorkflowName('New Workflow'); setWorkflowDesc('');
        setIsBuiltin(false);
        setNodes(DEFAULT_NODES); setEdges(DEFAULT_EDGES);
      }
    } catch (err) { console.error('Delete failed', err); }
  }, [workflowId, setNodes, setEdges]);

  /* Run button — save + open chat */
  const handleRun = useCallback(async () => {
    const spec = buildSpec();
    if (!workflowId) {
      try { await saveWorkflow(spec); setWorkflowId(spec.id); setSavedWorkflows(await listWorkflows()); }
      catch { /* proceed */ }
    } else {
      try { await updateWorkflow(workflowId, spec); }
      catch { /* proceed */ }
    }
    setShowChat(true);
  }, [buildSpec, workflowId]);

  const currentSelectedNode = useMemo(
    () => selectedNode ? nodes.find(n => n.id === selectedNode.id) || null : null,
    [nodes, selectedNode],
  );

  const canEdit = !isBuiltin || devMode;

  /* ================================================================ */
  /*  Render                                                           */
  /* ================================================================ */

  return (
    <Box h="100%" w="100%" display="flex" bg={C.bg}>
      <style>{`
.pin-label-row:hover .pin-label-name { opacity: 0 !important; }
.pin-label-row:hover .pin-type-tooltip { opacity: 1 !important; }
`}</style>
      {/* Sidebar */}
      {showSidebar && (
        <Box w="220px" minW="220px" h="100%" bg="#242424" borderRight={`1px solid ${C.border}`}
          display="flex" flexDirection="column" overflowY="auto">

          {/* Workflow meta */}
          <Box p={2} borderBottom={`1px solid ${C.border}`}>
            <Text fontSize="13px" fontWeight="bold" color={C.muted} mb={1} textTransform="uppercase">Workflow</Text>
            <Input size="sm" value={workflowName} onChange={e => setWorkflowName(e.target.value)}
              bg="#1a1a1a" borderColor={C.border} fontSize="md" mb={1} color={C.text}
              readOnly={isBuiltin && !devMode} />
            <HStack gap={1}>
              <Button size="xs" onClick={handleSave} variant="outline" flex={1}
                borderColor={C.border} color={C.text} _hover={{ bg: '#333' }}
                disabled={!canEdit}>
                <HStack gap={1}>
                  <SaveIcon />
                  <Text>{devMode && isBuiltin ? 'Save\u00a0\u2699' : 'Save'}</Text>
                </HStack>
              </Button>
              <Button size="xs" onClick={handleRun} flex={1}
                bg={C.green + '33'} color={C.green} borderColor={C.green + '55'} variant="outline"
                _hover={{ bg: C.green + '55' }}>
                <HStack gap={1}><PlayIcon /><Text>Run</Text></HStack>
              </Button>
            </HStack>
            <Button size="xs" onClick={handleValidate} variant="outline" w="100%" mt={1}
              borderColor={validationErrors.length > 0 ? C.red + '88' : C.border}
              color={validationErrors.length > 0 ? C.red : C.text}
              _hover={{ bg: '#333' }}>
              <HStack gap={1}>
                <Text>{validationErrors.length > 0 ? `\u26a0 ${validationErrors.length} error${validationErrors.length > 1 ? 's' : ''}` : '\u2713 Validate'}</Text>
              </HStack>
            </Button>
          </Box>

          {/* Dev mode toggle */}
          <Box p={2} borderBottom={`1px solid ${C.border}`}>
            <HStack justify="space-between" cursor="pointer" onClick={() => setDevMode(!devMode)}>
              <HStack gap={1}>
                {devMode ? <UnlockIcon /> : <LockIcon />}
                <Text fontSize="13px" fontWeight="bold" color={devMode ? C.amber : C.muted}
                  textTransform="uppercase">Dev Mode</Text>
              </HStack>
              <div style={{
                width: 28, height: 14, borderRadius: 7,
                background: devMode ? C.amber : '#555',
                position: 'relative', cursor: 'pointer',
                transition: 'background 0.2s',
              }}>
                <div style={{
                  width: 10, height: 10, borderRadius: '50%',
                  background: '#fff', position: 'absolute', top: 2,
                  left: devMode ? 16 : 2, transition: 'left 0.2s',
                }} />
              </div>
            </HStack>
            {devMode && (
              <Text fontSize="13px" color={C.amber} mt={1}>Builtin graphs are editable</Text>
            )}
          </Box>

          {/* Node palette */}
          <Box p={2} borderBottom={`1px solid ${C.border}`} overflow="hidden"
            display="flex" flexDirection="column" maxH="40vh">
            <Text fontSize="13px" fontWeight="bold" color={C.muted} mb={1} textTransform="uppercase" flexShrink={0}>Nodes</Text>
            <Box overflowY="auto" flex={1} minH={0}>
              <VStack gap={0} align="stretch">
                {CATEGORY_ORDER.filter(cat => _nodeDefs.some(d => (d.category || 'General') === cat)).map(cat => {
                  const isOpen = expandedCat === cat;
                  return (
                    <Box key={cat}>
                      <HStack px={1} py={1} cursor="pointer" gap={1}
                        onClick={() => setExpandedCat(isOpen ? null : cat)}
                        _hover={{ bg: '#333' }} borderRadius="3px" mb={isOpen ? 0.5 : 0}>
                        <Text fontSize="10px" color={C.muted}>{isOpen ? '▼' : '▶'}</Text>
                        <Text fontSize="11px" fontWeight={600} color={isOpen ? C.text : C.muted}
                          textTransform="uppercase">{cat}</Text>
                        <Text fontSize="11px" color={C.muted} ml="auto">
                          {_nodeDefs.filter(d => (d.category || 'General') === cat).length}
                        </Text>
                      </HStack>
                      {isOpen && (
                        <VStack gap={0.5} align="stretch" mb={1}>
                          {_nodeDefs.filter(d => (d.category || 'General') === cat).map(def => (
                            <Box key={def.type} draggable
                              onDragStart={e => { e.dataTransfer.setData('application/workflow-node-type', def.type); e.dataTransfer.effectAllowed = 'move'; }}
                              px={2} py={1} borderRadius="3px"
                              border={`1px solid ${def.accent}33`} cursor="grab"
                              _hover={{ bg: `${def.accent}18`, borderColor: `${def.accent}66` }}
                              transition="all 0.1s"
                            >
                              <HStack gap={2}>
                                <Box w="6px" h="6px" borderRadius="1px" bg={def.accent} flexShrink={0} />
                                <Box>
                                  <Text fontSize="14px" fontWeight={500} color={C.text}>{def.label}</Text>
                                  <Text fontSize="13px" color={C.muted}>{def.description}</Text>
                                </Box>
                              </HStack>
                            </Box>
                          ))}
                        </VStack>
                      )}
                    </Box>
                  );
                })}
              </VStack>
            </Box>
          </Box>

          {/* Workflow list */}
          <Box p={2} flex={1} overflowY="auto">
            <Text fontSize="13px" fontWeight="bold" color={C.muted} mb={1} textTransform="uppercase">Workflows</Text>
            {savedWorkflows.length === 0 ? (
              <Text fontSize="14px" color={C.muted}>None yet</Text>
            ) : (
              <VStack gap={0.5} align="stretch">
                {savedWorkflows.map(wf => {
                  const wfBuiltin = (wf as any).builtin;
                  return (
                    <HStack key={wf.id} px={2} py={1} borderRadius="3px"
                      bg={workflowId === wf.id ? '#383838' : 'transparent'}
                      _hover={{ bg: '#333' }} cursor="pointer"
                      onClick={() => handleLoad(wf.id)} justify="space-between">
                      <HStack gap={1} overflow="hidden">
                        {wfBuiltin && (devMode ? <UnlockIcon /> : <LockIcon />)}
                        <Box overflow="hidden">
                          <Text fontSize="14px" fontWeight={500} color={C.text} lineClamp={1}>{wf.name}</Text>
                          <Text fontSize="13px" color={C.muted}>{wf.node_count}n · {wf.edge_count}e</Text>
                        </Box>
                      </HStack>
                      {(!wfBuiltin || devMode) && (
                        <IconButton aria-label="Delete" size="xs" variant="ghost"
                          onClick={e => { e.stopPropagation(); handleDelete(wf.id); }}>
                          <TrashIcon />
                        </IconButton>
                      )}
                    </HStack>
                  );
                })}
              </VStack>
            )}
          </Box>
        </Box>
      )}

      {/* Canvas */}
      <Box flex={1} h="100%" ref={wrapperRef} position="relative">
        <ReactFlow
          nodes={enrichedNodes}
          edges={enrichedEdges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          onInit={handleRfInit}
          onDragOver={onDragOver}
          onDrop={onDrop}
          onPaneContextMenu={onPaneContextMenu}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          isValidConnection={isValidConnection}
          defaultEdgeOptions={{ selectable: true, deletable: true }}
          deleteKeyCode={['Backspace', 'Delete']}
          colorMode="dark"
          proOptions={{ hideAttribution: true }}
          style={{ background: C.bg }}
        >
          <Background gap={20} size={1} color={C.grid} />
          <Controls style={{ background: '#242424', border: `1px solid ${C.border}`, borderRadius: 4 }} />
          <MiniMap style={{ background: '#242424', border: `1px solid ${C.border}`, borderRadius: 4 }}
            maskColor="rgba(0,0,0,0.5)"
            nodeColor={n => _nodeDefMap[n.type || '']?.accent || '#555'} />
          <Panel position="top-left">
            <HStack gap={1}>
              <Button size="xs" variant="ghost" onClick={() => setShowSidebar(!showSidebar)}
                color={C.muted} _hover={{ bg: '#333' }} fontSize="14px">
                {showSidebar ? '\u25c0 Hide' : '\u25b6 Panel'}
              </Button>
              <Button size="xs" variant="ghost" onClick={() => setShowChat(!showChat)}
                color={showChat ? C.cyan : C.muted} _hover={{ bg: '#333' }} fontSize="14px">
                <HStack gap={1}><ChatIcon /><Text>{showChat ? 'Hide Chat' : 'Test Chat'}</Text></HStack>
              </Button>
            </HStack>
          </Panel>
          {isBuiltin && !devMode && (
            <Panel position="top-center">
              <div style={{
                background: C.amber + '22', border: `1px solid ${C.amber}44`, borderRadius: 4,
                padding: '3px 10px', fontSize: 14, color: C.amber,
              }}>
                Read-only builtin — enable Dev Mode to edit
              </div>
            </Panel>
          )}
        </ReactFlow>

        {/* Context menu */}
        {ctxMenu && (
          <ContextMenu
            x={ctxMenu.x} y={ctxMenu.y}
            flowX={ctxMenu.flowX} flowY={ctxMenu.flowY}
            onAddNode={addNode}
            onClose={() => setCtxMenu(null)}
          />
        )}

        {/* Run log */}
        {(runLog.length > 0 || lastAudit) && (
          <Box position="absolute" bottom={3} left={3}
            right={currentSelectedNode ? '240px' : (showChat ? '320px' : '3px')}
            maxH="200px" overflowY="auto"
            bg="#242424ee" border={`1px solid ${C.border}`} borderRadius="4px"
            p={2} fontSize="14px" fontFamily="monospace" zIndex={10}>
            <HStack justify="space-between" mb={1}>
              <Text fontWeight="bold" color={C.text} fontSize="14px">Run Log</Text>
              <HStack gap={1}>
                {lastAudit && (
                  <Text fontSize="11px" color={C.cyan}>
                    {lastAudit.total_duration_ms?.toFixed(0)}ms
                  </Text>
                )}
                <Button size="xs" variant="ghost" onClick={() => { setRunLog([]); setLastAudit(null); }} color={C.muted}>Clear</Button>
              </HStack>
            </HStack>
            {runLog.map((line, i) => (
              <Text key={i} color={
                line.includes('\u2717') ? C.red :
                line.includes('\u2713') ? C.green : C.muted
              }>{line}</Text>
            ))}
            {lastAudit && (
              <Box mt={1} pt={1} borderTop={`1px solid ${C.border}`}>
                <Text fontSize="11px" color={C.cyan} mb={1}>
                  Audit: {lastAudit.request_id} \u2022 {lastAudit.total_duration_ms?.toFixed(0)}ms total
                </Text>
                {lastAudit.events
                  ?.filter((e: any) => e.phase === 'node')
                  .map((e: any, i: number) => (
                    <Text key={i} fontSize="11px" color={C.muted}>
                      {e.elapsed_ms?.toFixed(0).padStart(6)}ms {e.event}
                      {e.label ? ` (${e.label})` : ''}
                      {e.duration_ms != null ? ` ${e.duration_ms.toFixed(0)}ms` : ''}
                    </Text>
                  ))}
              </Box>
            )}
          </Box>
        )}

        {/* Validation errors */}
        {showValidation && (
          <Box position="absolute" bottom={(runLog.length > 0 || lastAudit) ? '210px' : '3px'} left={3}
            right={currentSelectedNode ? '240px' : (showChat ? '320px' : '3px')}
            maxH="180px" overflowY="auto"
            bg={validationErrors.length > 0 ? '#2a1515ee' : '#1a2a1aee'}
            border={`1px solid ${validationErrors.length > 0 ? C.red + '66' : C.green + '66'}`}
            borderRadius="4px"
            p={2} fontSize="14px" zIndex={11}>
            <HStack justify="space-between" mb={1}>
              <Text fontWeight="bold" color={validationErrors.length > 0 ? C.red : C.green} fontSize="14px">
                {validationErrors.length > 0
                  ? `${validationErrors.length} type error${validationErrors.length > 1 ? 's' : ''}`
                  : '\u2713 All edges valid'}
              </Text>
              <Button size="xs" variant="ghost" onClick={() => setShowValidation(false)} color={C.muted}>Close</Button>
            </HStack>
            {validationErrors.map((err, i) => (
              <Box key={i} py={0.5} cursor="pointer" _hover={{ bg: '#ffffff08' }} borderRadius="2px" px={1}
                onClick={() => {
                  if (err.edge_id) {
                    setEdges(eds => eds.map(e => ({
                      ...e,
                      selected: e.id === err.edge_id,
                    })));
                  }
                }}>
                <Text fontSize="13px" color={C.red}>
                  {err.message}
                </Text>
              </Box>
            ))}
          </Box>
        )}
      </Box>

      {/* Chat / Test panel — reuses the real ChatPanel */}
      {showChat && (
        <Box w="420px" minW="420px" h="100%"
          borderLeft={`1px solid ${C.border}`} display="flex" flexDirection="column">
          <Box p={1} borderBottom={`1px solid ${C.border}`} display="flex" justifyContent="space-between" alignItems="center">
            <HStack gap={1} pl={2}>
              <ChatIcon />
              <Text fontSize="14px" fontWeight="bold" color={C.text}>
                Test Chat
              </Text>
              {workflowId && (
                <Text fontSize="12px" color={C.muted}>
                  ({nodes.find(n => n.type === 'start')?.data?.label || workflowId})
                </Text>
              )}
            </HStack>
            <HStack gap={1}>
              <Button size="xs" variant="ghost" color={C.muted}
                onClick={() => { setTestChatKey(k => k + 1); setRunLog([]); }}
                _hover={{ bg: '#333' }} fontSize="13px">New</Button>
              <Button size="xs" variant="ghost" color={C.muted}
                onClick={() => setShowChat(false)}
                _hover={{ bg: '#333' }} fontSize="14px">{'\u2715'}</Button>
            </HStack>
          </Box>
          <Box flex={1} overflow="hidden">
            <ChatPanel
              key={`test-${workflowId}-${testChatKey}`}
              conversationId={null}
              compact
              ephemeral
              initialGraph={workflowId ? `workflow:${workflowId}` : undefined}
              placeholder="Send a message to test the workflow..."
            />
          </Box>
        </Box>
      )}

      {/* Property panel */}
      {currentSelectedNode && !showChat && (
        <Box w="230px" minW="230px" h="100%" bg="#242424"
          borderLeft={`1px solid ${C.border}`} overflowY="auto">
          <PropPanel node={currentSelectedNode} onUpdate={updateNodeData} onDelete={deleteNode} />
        </Box>
      )}
    </Box>
  );
};

export default WorkflowEditor;
