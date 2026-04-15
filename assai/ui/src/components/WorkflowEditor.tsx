import { FC, useCallback, useRef, useState, useEffect, useMemo, DragEvent, CSSProperties } from 'react';
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
  runWorkflow,
  saveBuiltinWorkflow,
  type WorkflowSummary,
  type WorkflowSpec,
} from '../services/api';

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
/*  Pin definitions per node type                                      */
/* ================================================================== */

interface PinDef {
  id: string;          // handle id, e.g. "data_message"
  label: string;
  color: string;
  side: 'left' | 'right';
  kind: 'exec' | 'data';
}

interface NodeDef {
  type: string;
  label: string;
  accent: string;
  pins: PinDef[];
  description: string;
}

const NODE_DEFS: NodeDef[] = [
  {
    type: 'start', label: 'Start', accent: C.green, description: 'Entry point',
    pins: [
      { id: 'exec_out',          label: '',             color: C.white, side: 'right', kind: 'exec' },
      { id: 'data_conversation',  label: 'conversation', color: C.blue,  side: 'right', kind: 'data' },
      { id: 'data_message',       label: 'message',      color: C.amber, side: 'right', kind: 'data' },
    ],
  },
  {
    type: 'agent', label: 'Agent', accent: C.blue, description: 'LLM agent call',
    pins: [
      { id: 'exec_in',      label: '',        color: C.white,  side: 'left',  kind: 'exec' },
      { id: 'exec_out',     label: '',        color: C.white,  side: 'right', kind: 'exec' },
      { id: 'data_agent',   label: 'agent',   color: C.cyan,   side: 'left',  kind: 'data' },
      { id: 'data_context', label: 'context', color: C.blue,   side: 'left',  kind: 'data' },
      { id: 'data_stream',  label: 'stream',  color: C.green,  side: 'right', kind: 'data' },
    ],
  },
  {
    type: 'forward', label: 'Forward', accent: C.purple, description: 'Stream to user',
    pins: [
      { id: 'exec_in',     label: '',       color: C.white,  side: 'left',  kind: 'exec' },
      { id: 'exec_out',    label: '',       color: C.white,  side: 'right', kind: 'exec' },
      { id: 'data_stream', label: 'stream', color: C.green,  side: 'left',  kind: 'data' },
    ],
  },
  {
    type: 'accumulate', label: 'Accumulate', accent: C.green, description: 'Stream to response',
    pins: [
      { id: 'exec_in',       label: '',         color: C.white,  side: 'left',  kind: 'exec' },
      { id: 'exec_out',      label: '',         color: C.white,  side: 'right', kind: 'exec' },
      { id: 'data_stream',   label: 'stream',   color: C.green,  side: 'left',  kind: 'data' },
      { id: 'data_response', label: 'response', color: C.amber,  side: 'right', kind: 'data' },
    ],
  },
  {
    type: 'tool', label: 'Tool', accent: C.amber, description: 'Single tool call',
    pins: [
      { id: 'exec_in',     label: '',       color: C.white,  side: 'left',  kind: 'exec' },
      { id: 'exec_out',    label: '',       color: C.white,  side: 'right', kind: 'exec' },
      { id: 'data_tool',   label: 'tool',   color: C.cyan,   side: 'left',  kind: 'data' },
      { id: 'data_input',  label: 'input',  color: C.green,  side: 'left',  kind: 'data' },
      { id: 'data_result', label: 'result', color: C.green,  side: 'right', kind: 'data' },
    ],
  },
  {
    type: 'append', label: 'Append', accent: C.purple, description: 'Append item to array',
    pins: [
      { id: 'exec_in',     label: '',       color: C.white,  side: 'left',  kind: 'exec' },
      { id: 'exec_out',    label: '',       color: C.white,  side: 'right', kind: 'exec' },
      { id: 'data_a',      label: 'array',  color: C.blue,   side: 'left',  kind: 'data' },
      { id: 'data_b',      label: 'item',   color: C.amber,  side: 'left',  kind: 'data' },
      { id: 'data_result', label: 'result', color: C.blue,   side: 'right', kind: 'data' },
    ],
  },
  {
    type: 'condition', label: 'Condition', accent: C.red, description: 'Branch on expression',
    pins: [
      { id: 'exec_in',    label: '',      color: C.white, side: 'left',  kind: 'exec' },
      { id: 'exec_true',  label: 'true',  color: C.green, side: 'right', kind: 'exec' },
      { id: 'exec_false', label: 'false', color: C.red,   side: 'right', kind: 'exec' },
      { id: 'data_value', label: 'value', color: C.green, side: 'left',  kind: 'data' },
    ],
  },
  {
    type: 'output', label: 'Output', accent: C.cyan, description: 'Final response',
    pins: [
      { id: 'exec_in',       label: '',         color: C.white, side: 'left', kind: 'exec' },
      { id: 'data_response', label: 'response', color: C.amber, side: 'left', kind: 'data' },
    ],
  },
];

const NODE_DEF_MAP = Object.fromEntries(NODE_DEFS.map(d => [d.type, d]));

function getPinDefs(nodeType: string): PinDef[] {
  return NODE_DEF_MAP[nodeType]?.pins || [];
}

/* ================================================================== */
/*  Custom edge components                                             */
/* ================================================================== */

function ExecEdge(props: EdgeProps) {
  const [path] = getBezierPath({
    sourceX: props.sourceX, sourceY: props.sourceY,
    targetX: props.targetX, targetY: props.targetY,
    sourcePosition: props.sourcePosition, targetPosition: props.targetPosition,
  });
  return <BaseEdge path={path} style={{ stroke: C.white, strokeWidth: 2 }} />;
}

function DataEdge(props: EdgeProps) {
  const color = (props.data as any)?.color || C.green;
  const [path] = getBezierPath({
    sourceX: props.sourceX, sourceY: props.sourceY,
    targetX: props.targetX, targetY: props.targetY,
    sourcePosition: props.sourcePosition, targetPosition: props.targetPosition,
  });
  return <BaseEdge path={path} style={{ stroke: color, strokeWidth: 1.5, opacity: 0.7 }} />;
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

function NodeShell({ def, selected, connectedHandles, data, onUpdate, pinWidgets, children }: NodeShellProps) {
  const connected = connectedHandles || new Set<string>();
  const widgets = pinWidgets || {};

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

  const nodeStyle: CSSProperties = {
    background: C.node,
    borderRadius: 4,
    border: `1px solid ${selected ? def.accent : C.border}`,
    width: totalW,
    fontSize: 11,
    overflow: 'visible',
  };

  const headerBg = def.accent + '26';

  return (
    <div style={nodeStyle}>
      {/* Header */}
      <div style={{
        height: HEADER_H, display: 'flex', alignItems: 'center',
        padding: '0 8px', background: headerBg,
        borderRadius: '3px 3px 0 0',
        borderBottom: `1px solid ${C.border}`,
      }}>
        <span style={{ color: C.text, fontWeight: 500, fontSize: 11, letterSpacing: '0.02em' }}>
          {def.label}
        </span>
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
                  {pin.label && (
                    <div style={{ fontSize: 10, color: pin.color, lineHeight: '14px' }}>{pin.label}</div>
                  )}
                  {showField && (
                    <input
                      style={{
                        width: '100%', fontSize: 10, padding: '2px 4px', marginTop: 2,
                        background: '#222', border: `1px solid ${C.border}`, borderRadius: 2,
                        color: C.text, outline: 'none', height: 20, boxSizing: 'border-box',
                      }}
                      value={String((data as any)[key] ?? '')}
                      onChange={e => onUpdate?.({ ...data, [key]: e.target.value })}
                      placeholder={key}
                    />
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
                {pin.label && (
                  <div style={{ fontSize: 10, color: pin.color, lineHeight: '14px' }}>{pin.label}</div>
                )}
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

  const shapeStyle: CSSProperties = isExec ? {
    width: 0, height: 0,
    borderTop: '5px solid transparent',
    borderBottom: '5px solid transparent',
    borderLeft: `7px solid ${isConnected ? pin.color : 'transparent'}`,
    borderRight: 'none',
    background: 'transparent',
    borderRadius: 0,
  } : {
    width: 8, height: 8,
    borderRadius: '50%',
    background: isConnected ? pin.color : 'transparent',
    border: `1.5px solid ${pin.color}`,
  };

  const execStroke: CSSProperties | null = isExec && !isConnected ? {
    width: 0, height: 0,
    borderTop: '5px solid transparent',
    borderBottom: '5px solid transparent',
    borderLeft: `7px solid ${pin.color}`,
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
    data_conversation: (
      <input style={outputFieldStyle}
        value={String(data.preview_conversation || '')}
        onChange={e => onUpdate?.({ ...data, preview_conversation: e.target.value })}
        placeholder="[]" />
    ),
    data_message: (
      <input style={outputFieldStyle}
        value={String(data.preview_message || '')}
        onChange={e => onUpdate?.({ ...data, preview_message: e.target.value })}
        placeholder="Hello!" />
    ),
  };
  return (
    <NodeShell def={NODE_DEF_MAP['start']} selected={selected}
      {...nodeProps(data)} pinWidgets={pinWidgets} />
  );
}

function AgentNode({ data, selected }: NodeProps) {
  return <NodeShell def={NODE_DEF_MAP['agent']} selected={selected} {...nodeProps(data)} />;
}

function ToolNode({ data, selected }: NodeProps) {
  return <NodeShell def={NODE_DEF_MAP['tool']} selected={selected} {...nodeProps(data)} />;
}

function ForwardNode({ data, selected }: NodeProps) {
  return <NodeShell def={NODE_DEF_MAP['forward']} selected={selected} {...nodeProps(data)} />;
}

function AccumulateNode({ data, selected }: NodeProps) {
  return <NodeShell def={NODE_DEF_MAP['accumulate']} selected={selected} {...nodeProps(data)} />;
}

function AppendNode({ data, selected }: NodeProps) {
  return <NodeShell def={NODE_DEF_MAP['append']} selected={selected} {...nodeProps(data)} />;
}

function ConditionNode({ data, selected }: NodeProps) {
  return <NodeShell def={NODE_DEF_MAP['condition']} selected={selected} {...nodeProps(data)} />;
}

function OutputNode({ data, selected }: NodeProps) {
  return <NodeShell def={NODE_DEF_MAP['output']} selected={selected} {...nodeProps(data)} />;
}

const nodeTypes: NodeTypes = {
  start: StartNode,
  agent: AgentNode,
  forward: ForwardNode,
  accumulate: AccumulateNode,
  tool: ToolNode,
  append: AppendNode,
  condition: ConditionNode,
  output: OutputNode,
};

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
const SendIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" /></svg>
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

function ContextMenu({ x, y, flowX, flowY, onAddNode, onClose }: ContextMenuProps) {
  useEffect(() => {
    const handle = () => onClose();
    window.addEventListener('click', handle);
    return () => window.removeEventListener('click', handle);
  }, [onClose]);

  return (
    <div style={{
      position: 'fixed', left: x, top: y, zIndex: 1000,
      background: '#2a2a2a', border: `1px solid ${C.border}`, borderRadius: 4,
      minWidth: 160, padding: '4px 0', boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
    }} onClick={e => e.stopPropagation()}>
      <div style={{ fontSize: 9, color: C.muted, padding: '4px 12px', textTransform: 'uppercase', fontWeight: 600 }}>
        Add Node
      </div>
      {NODE_DEFS.map(def => (
        <div key={def.type}
          onClick={() => { onAddNode(def.type, { x: flowX, y: flowY }); onClose(); }}
          style={{
            padding: '5px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
            fontSize: 11, color: C.text,
          }}
          onMouseEnter={e => (e.currentTarget.style.background = '#383838')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
        >
          <div style={{ width: 6, height: 6, borderRadius: 1, background: def.accent, flexShrink: 0 }} />
          <div>
            <div style={{ fontWeight: 500 }}>{def.label}</div>
            <div style={{ fontSize: 9, color: C.muted }}>{def.description}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

/* ================================================================== */
/*  Chat message                                                       */
/* ================================================================== */

interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'reasoning';
  content: string;
  nodeLabel?: string;
}

function ChatBubble({ msg, streaming }: { msg: ChatMessage; streaming?: boolean }) {
  const isUser = msg.role === 'user';
  const isReasoning = msg.role === 'reasoning';
  const [expanded, setExpanded] = useState(false);

  if (isReasoning) {
    const label = msg.nodeLabel ? `${msg.nodeLabel} thinking` : 'thinking';
    const preview = msg.content.length > 80
      ? msg.content.slice(0, 80) + '…'
      : msg.content;
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-start', padding: '2px 0' }}>
        <div style={{
          maxWidth: '85%', borderRadius: 6, overflow: 'hidden',
          background: C.purple + '12', border: `1px solid ${C.purple}33`,
        }}>
          <div
            onClick={() => !streaming && setExpanded(e => !e)}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '5px 10px', cursor: streaming ? 'default' : 'pointer',
              userSelect: 'none',
            }}
          >
            <span style={{
              fontSize: 8, color: C.purple, transition: 'transform .15s',
              display: 'inline-block',
              transform: expanded || streaming ? 'rotate(90deg)' : 'rotate(0deg)',
            }}>&#9654;</span>
            <span style={{ fontSize: 10, color: C.purple, fontWeight: 500 }}>{label}</span>
            {!expanded && !streaming && (
              <span style={{ fontSize: 10, color: C.purple + '88', fontStyle: 'italic', marginLeft: 2 }}>
                {preview}
              </span>
            )}
          </div>
          {(expanded || streaming) && (
            <div style={{
              padding: '2px 10px 8px 22px',
              fontSize: 10, lineHeight: '15px', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              color: C.purple + 'cc', fontStyle: 'italic',
              maxHeight: 300, overflowY: 'auto',
            }}>
              {msg.content}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start',
      padding: '2px 0',
    }}>
      <div style={{
        maxWidth: '85%', padding: '6px 10px', borderRadius: 6,
        fontSize: 11, lineHeight: '16px', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        background: isUser ? C.blue + '33' : '#333',
        color: C.text,
        border: `1px solid ${isUser ? C.blue + '44' : C.border}`,
      }}>
        {msg.role === 'system' && <div style={{ fontSize: 9, color: C.muted, marginBottom: 2 }}>system</div>}
        {msg.role === 'assistant' && msg.nodeLabel && (
          <div style={{ fontSize: 9, color: C.muted, marginBottom: 2 }}>{msg.nodeLabel}</div>
        )}
        {msg.content}
      </div>
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
      <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>{label}</div>
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
      <Text fontSize="10px" color={C.muted}>{ntype} node</Text>

      {field('Label', 'label', { placeholder: ntype })}

      {ntype === 'start' && (
        <>
          {field('Preview Message', 'preview_message', { placeholder: 'Test user message', rows: 3 })}
          {field('Preview Conversation', 'preview_conversation', { placeholder: 'Prior context (optional)', rows: 4 })}
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
          <div style={{ fontSize: 10, color: C.muted, marginBottom: 2 }}>Mode</div>
          <HStack gap={1}>
            {['token', 'reasoning'].map(mode => (
              <Button key={mode} size="xs" variant="outline" flex={1}
                bg={(data as any).mode === mode || (!((data as any).mode) && mode === 'token') ? (mode === 'reasoning' ? C.purple + '33' : C.green + '33') : 'transparent'}
                borderColor={(data as any).mode === mode || (!((data as any).mode) && mode === 'token') ? (mode === 'reasoning' ? C.purple : C.green) : C.border}
                color={(data as any).mode === mode || (!((data as any).mode) && mode === 'token') ? (mode === 'reasoning' ? C.purple : C.green) : C.muted}
                onClick={() => onUpdate(node.id, { ...data, mode })}
                _hover={{ bg: '#333' }} fontSize="10px"
              >{mode}</Button>
            ))}
          </HStack>
          <Text fontSize="9px" color={C.muted} mt={1}>
            token = visible reply, reasoning = thinking bubble
          </Text>
        </div>
      )}
      {ntype === 'condition' && (
        <>
          {field('Expression', 'expression', { placeholder: 'len(input) > 100', mono: true })}
          <Text fontSize="9px" color={C.muted}>Variables: input, len</Text>
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

async function parseSSE(
  response: Response,
  onEvent: (eventType: string, data: any) => void,
) {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop()!;
    for (const frame of frames) {
      if (!frame.trim()) continue;
      let eventType = 'message';
      const dataLines: string[] = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) eventType = line.slice(7).trim();
        else if (line.startsWith('data: ')) dataLines.push(line.slice(6));
      }
      let data: any = {};
      try { data = JSON.parse(dataLines.join('\n')); } catch { /* skip */ }
      onEvent(eventType, data);
    }
  }
}

/* ================================================================== */
/*  Main editor                                                        */
/* ================================================================== */

const WorkflowEditor: FC = () => {
  const [nodes, setNodes, onNodesChange] = useNodesState(DEFAULT_NODES);
  const [edges, setEdges, onEdgesChange] = useEdgesState(DEFAULT_EDGES);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const [workflowId, setWorkflowId] = useState('');
  const [workflowName, setWorkflowName] = useState('New Workflow');
  const [workflowDesc, setWorkflowDesc] = useState('');
  const [savedWorkflows, setSavedWorkflows] = useState<WorkflowSummary[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [showSidebar, setShowSidebar] = useState(true);
  const [devMode, setDevMode] = useState(false);
  const [isBuiltin, setIsBuiltin] = useState(false);

  /* Context menu */
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; flowX: number; flowY: number } | null>(null);

  /* Chat panel */
  const [showChat, setShowChat] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState('');
  const chatEndRef = useRef<HTMLDivElement>(null);
  const [runLog, setRunLog] = useState<string[]>([]);
  const [streamingText, setStreamingText] = useState('');
  const [streamingReasoning, setStreamingReasoning] = useState('');
  const [streamingNodeLabel, setStreamingNodeLabel] = useState('');

  useEffect(() => {
    listWorkflows().then(setSavedWorkflows).catch(() => {});
  }, []);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages, streamingText, streamingReasoning]);

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

  const enrichedNodes = useMemo(() =>
    nodes.map(n => ({
      ...n,
      data: {
        ...n.data,
        _connected: connectedMap[n.id] || new Set(),
        _onUpdate: (d: Record<string, unknown>) => updateNodeData(n.id, d),
      },
    })),
  [nodes, connectedMap, updateNodeData]);

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
    const defaultData: Record<string, unknown> = { label: NODE_DEF_MAP[type]?.label || type };
    if (type === 'agent') defaultData.agent = 'default';
    if (type === 'condition') defaultData.expression = 'True';
    if (type === 'start') { defaultData.preview_message = 'Hello!'; defaultData.preview_conversation = ''; }
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
  }, [setNodes, setEdges]);

  const handleLoad = useCallback(async (id: string) => {
    try { loadSpec(await getWorkflow(id)); } catch (err) { console.error('Load failed', err); }
  }, [loadSpec]);

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

  /* Run workflow from the chat panel */
  const handleRunChat = useCallback(async (message: string) => {
    if (!message.trim()) return;
    const spec = buildSpec();
    const specId = spec.id;

    if (!workflowId) {
      try { await saveWorkflow(spec); setWorkflowId(specId); setSavedWorkflows(await listWorkflows()); }
      catch { /* proceed */ }
    }

    const userMsg: ChatMessage = { role: 'user', content: message };
    setChatMessages(prev => [...prev, userMsg]);
    setChatInput('');
    setIsRunning(true);
    setStreamingText('');
    setStreamingReasoning('');
    setStreamingNodeLabel('');
    setRunLog(['\u25b6 Starting workflow...']);

    const conversation = chatMessages
      .filter(m => m.role !== 'system' && m.role !== 'reasoning')
      .map(m => ({ role: m.role, content: m.content }));
    conversation.push({ role: 'user', content: message });
    const conversationJson = JSON.stringify(conversation);

    let accTokens = '';
    let accReasoning = '';
    let currentNodeLabel = '';
    let finalOutput = '';
    const finishedMessages: ChatMessage[] = [];

    try {
      const response = await runWorkflow(specId, message, conversationJson);

      await parseSSE(response, (eventType, data) => {
        if (eventType === 'workflow_start') {
          setRunLog(p => [...p, `\u2699 ${data.name} (${data.node_count} nodes)`]);

        } else if (eventType === 'node_start') {
          currentNodeLabel = data.label || data.node_id;
          setStreamingNodeLabel(currentNodeLabel);
          setRunLog(p => [...p, `  \u2192 ${currentNodeLabel} [${data.type}]`]);

          if (data.type === 'forward') {
            accTokens = '';
            accReasoning = '';
            setStreamingText('');
            setStreamingReasoning('');
          }

        } else if (eventType === 'agent_token') {
          const token = data.token || '';
          if (data.stream_mode === 'reasoning') {
            accReasoning += token;
            setStreamingReasoning(accReasoning);
          } else {
            accTokens += token;
            setStreamingText(accTokens);
          }

        } else if (eventType === 'node_end') {
          const pv = data.output_preview ? `: ${data.output_preview.slice(0, 120)}` : '';
          setRunLog(p => [...p, `  \u2713 ${data.node_id}${pv}`]);

          if (data.type === 'forward') {
            if (accReasoning) {
              finishedMessages.push({
                role: 'reasoning', content: accReasoning,
                nodeLabel: currentNodeLabel,
              });
            }
            if (accTokens) {
              finishedMessages.push({
                role: 'assistant', content: accTokens,
                nodeLabel: currentNodeLabel,
              });
            }
            setStreamingText('');
            setStreamingReasoning('');
            accTokens = '';
            accReasoning = '';
          }
          if (data.final_output) finalOutput = data.final_output;

        } else if (eventType === 'workflow_end') {
          setRunLog(p => [...p, '\u2713 Finished']);
          if (data.output && !finalOutput) finalOutput = data.output;

        } else if (eventType === 'error') {
          setRunLog(p => [...p, `\u2717 ${data.message || 'Error'}`]);

        } else if (eventType === 'done') {
          setRunLog(p => [...p, '\u2014 Done \u2014']);
        }
      });

      setChatMessages(prev => {
        const next = [...prev, ...finishedMessages];
        if (finalOutput && !finishedMessages.some(
          m => m.role === 'assistant' && m.content === finalOutput,
        )) {
          next.push({ role: 'assistant', content: finalOutput });
        }
        return next;
      });
    } catch (err: any) {
      setRunLog(p => [...p, `\u2717 ${err.message || 'Failed'}`]);
      setChatMessages(prev => [...prev, { role: 'system', content: `Error: ${err.message || 'Failed'}` }]);
    } finally {
      setIsRunning(false);
      setStreamingText('');
      setStreamingReasoning('');
    }
  }, [buildSpec, workflowId, chatMessages]);

  /* Legacy Run button (uses preview inputs from Start node) */
  const handleRun = useCallback(async () => {
    const startNode = nodes.find(n => n.type === 'start');
    const previewMsg = String(startNode?.data?.preview_message || 'Hello!');
    setShowChat(true);
    await handleRunChat(previewMsg);
  }, [nodes, handleRunChat]);

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
      {/* Sidebar */}
      {showSidebar && (
        <Box w="220px" minW="220px" h="100%" bg="#242424" borderRight={`1px solid ${C.border}`}
          display="flex" flexDirection="column" overflowY="auto">

          {/* Workflow meta */}
          <Box p={2} borderBottom={`1px solid ${C.border}`}>
            <Text fontSize="9px" fontWeight="bold" color={C.muted} mb={1} textTransform="uppercase">Workflow</Text>
            <Input size="sm" value={workflowName} onChange={e => setWorkflowName(e.target.value)}
              bg="#1a1a1a" borderColor={C.border} fontSize="xs" mb={1} color={C.text}
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
              <Button size="xs" onClick={handleRun} flex={1} disabled={isRunning}
                bg={C.green + '33'} color={C.green} borderColor={C.green + '55'} variant="outline"
                _hover={{ bg: C.green + '55' }}>
                <HStack gap={1}><PlayIcon /><Text>{isRunning ? 'Running' : 'Run'}</Text></HStack>
              </Button>
            </HStack>
          </Box>

          {/* Dev mode toggle */}
          <Box p={2} borderBottom={`1px solid ${C.border}`}>
            <HStack justify="space-between" cursor="pointer" onClick={() => setDevMode(!devMode)}>
              <HStack gap={1}>
                {devMode ? <UnlockIcon /> : <LockIcon />}
                <Text fontSize="9px" fontWeight="bold" color={devMode ? C.amber : C.muted}
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
              <Text fontSize="9px" color={C.amber} mt={1}>Builtin graphs are editable</Text>
            )}
          </Box>

          {/* Node palette */}
          <Box p={2} borderBottom={`1px solid ${C.border}`}>
            <Text fontSize="9px" fontWeight="bold" color={C.muted} mb={1} textTransform="uppercase">Nodes</Text>
            <VStack gap={0.5} align="stretch">
              {NODE_DEFS.map(def => (
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
                      <Text fontSize="10px" fontWeight={500} color={C.text}>{def.label}</Text>
                      <Text fontSize="9px" color={C.muted}>{def.description}</Text>
                    </Box>
                  </HStack>
                </Box>
              ))}
            </VStack>
          </Box>

          {/* Workflow list */}
          <Box p={2} flex={1} overflowY="auto">
            <Text fontSize="9px" fontWeight="bold" color={C.muted} mb={1} textTransform="uppercase">Workflows</Text>
            {savedWorkflows.length === 0 ? (
              <Text fontSize="10px" color={C.muted}>None yet</Text>
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
                          <Text fontSize="10px" fontWeight={500} color={C.text} lineClamp={1}>{wf.name}</Text>
                          <Text fontSize="9px" color={C.muted}>{wf.node_count}n · {wf.edge_count}e</Text>
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
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          onInit={setRfInstance}
          onDragOver={onDragOver}
          onDrop={onDrop}
          onPaneContextMenu={onPaneContextMenu}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          isValidConnection={isValidConnection}
          fitView
          colorMode="dark"
          proOptions={{ hideAttribution: true }}
          style={{ background: C.bg }}
        >
          <Background gap={20} size={1} color={C.grid} />
          <Controls style={{ background: '#242424', border: `1px solid ${C.border}`, borderRadius: 4 }} />
          <MiniMap style={{ background: '#242424', border: `1px solid ${C.border}`, borderRadius: 4 }}
            maskColor="rgba(0,0,0,0.5)"
            nodeColor={n => NODE_DEF_MAP[n.type || '']?.accent || '#555'} />
          <Panel position="top-left">
            <HStack gap={1}>
              <Button size="xs" variant="ghost" onClick={() => setShowSidebar(!showSidebar)}
                color={C.muted} _hover={{ bg: '#333' }} fontSize="10px">
                {showSidebar ? '\u25c0 Hide' : '\u25b6 Panel'}
              </Button>
              <Button size="xs" variant="ghost" onClick={() => setShowChat(!showChat)}
                color={showChat ? C.cyan : C.muted} _hover={{ bg: '#333' }} fontSize="10px">
                <HStack gap={1}><ChatIcon /><Text>{showChat ? 'Hide Chat' : 'Test Chat'}</Text></HStack>
              </Button>
            </HStack>
          </Panel>
          {isBuiltin && !devMode && (
            <Panel position="top-center">
              <div style={{
                background: C.amber + '22', border: `1px solid ${C.amber}44`, borderRadius: 4,
                padding: '3px 10px', fontSize: 10, color: C.amber,
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
        {runLog.length > 0 && (
          <Box position="absolute" bottom={3} left={3}
            right={currentSelectedNode ? '240px' : (showChat ? '320px' : '3px')}
            maxH="140px" overflowY="auto"
            bg="#242424ee" border={`1px solid ${C.border}`} borderRadius="4px"
            p={2} fontSize="10px" fontFamily="monospace" zIndex={10}>
            <HStack justify="space-between" mb={1}>
              <Text fontWeight="bold" color={C.text} fontSize="10px">Run Log</Text>
              <Button size="xs" variant="ghost" onClick={() => setRunLog([])} color={C.muted}>Clear</Button>
            </HStack>
            {runLog.map((line, i) => (
              <Text key={i} color={
                line.includes('\u2717') ? C.red :
                line.includes('\u2713') ? C.green : C.muted
              }>{line}</Text>
            ))}
          </Box>
        )}
      </Box>

      {/* Chat / Test panel */}
      {showChat && (
        <Box w="310px" minW="310px" h="100%" bg="#242424"
          borderLeft={`1px solid ${C.border}`} display="flex" flexDirection="column">
          {/* Header */}
          <Box p={2} borderBottom={`1px solid ${C.border}`}>
            <HStack justify="space-between">
              <HStack gap={1}>
                <ChatIcon />
                <Text fontSize="11px" fontWeight="bold" color={C.text}>Test Chat</Text>
              </HStack>
              <HStack gap={1}>
                <Button size="xs" variant="ghost" color={C.muted} onClick={() => { setChatMessages([]); setRunLog([]); }}
                  _hover={{ bg: '#333' }} fontSize="9px">Clear</Button>
                <Button size="xs" variant="ghost" color={C.muted} onClick={() => setShowChat(false)}
                  _hover={{ bg: '#333' }} fontSize="10px">\u2715</Button>
              </HStack>
            </HStack>
          </Box>

          {/* Messages */}
          <Box flex={1} overflowY="auto" p={2} display="flex" flexDirection="column" gap={1}>
            {chatMessages.length === 0 && (
              <Box textAlign="center" py={8}>
                <Text fontSize="10px" color={C.muted}>Send a message to test the workflow</Text>
                <Text fontSize="9px" color={C.muted} mt={1}>
                  Your message is passed as the user input
                </Text>
              </Box>
            )}
            {chatMessages.map((msg, i) => (
              <ChatBubble key={i} msg={msg} />
            ))}
            {isRunning && streamingReasoning && (
              <ChatBubble streaming msg={{
                role: 'reasoning', content: streamingReasoning + '\u2588',
                nodeLabel: streamingNodeLabel,
              }} />
            )}
            {isRunning && streamingText && (
              <ChatBubble msg={{
                role: 'assistant', content: streamingText + '\u2588',
                nodeLabel: streamingNodeLabel,
              }} />
            )}
            {isRunning && !streamingText && !streamingReasoning && (
              <div style={{
                display: 'flex', justifyContent: 'flex-start', padding: '2px 0',
              }}>
                <div style={{
                  padding: '6px 14px', borderRadius: 6, fontSize: 11,
                  background: '#333', color: C.muted, border: `1px solid ${C.border}`,
                }}>
                  {streamingNodeLabel
                    ? `${streamingNodeLabel} \u2026`
                    : '\u2026 starting'}
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </Box>

          {/* Input */}
          <Box p={2} borderTop={`1px solid ${C.border}`}>
            <HStack gap={1}>
              <input
                style={{
                  flex: 1, fontSize: 11, padding: '6px 10px', height: 32,
                  background: '#1a1a1a', border: `1px solid ${C.border}`, borderRadius: 4,
                  color: C.text, outline: 'none',
                }}
                value={chatInput}
                onChange={e => setChatInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !isRunning) { e.preventDefault(); handleRunChat(chatInput); } }}
                placeholder="Type a message..."
                disabled={isRunning}
              />
              <Button size="xs" h="32px" px={3} onClick={() => handleRunChat(chatInput)}
                disabled={isRunning || !chatInput.trim()}
                bg={C.cyan + '33'} color={C.cyan} borderColor={C.cyan + '55'} variant="outline"
                _hover={{ bg: C.cyan + '55' }}>
                <SendIcon />
              </Button>
            </HStack>
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
