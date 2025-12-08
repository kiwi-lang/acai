import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { Box, HStack, Text, VStack, IconButton, Input } from '@chakra-ui/react';
import { assaiAPI } from '../services/api';

interface TelemetryData {
    cpu: {
        memory: [number, number];
        load: number;
    };
    gpu: Record<string, {
        memory: [number, number];
        load: number;
        temp: number;
        power: number;
    }>;
}

interface SparklineProps {
    values: number[];
    color: string;
    width?: number;
    height?: number;
    fill?: boolean;
}

const Sparkline = ({ values, color, width = 60, height = 20, fill = false }: SparklineProps) => {
    const { points, fillPath } = useMemo(() => {
        if (values.length === 0) {
            return { points: '', fillPath: '' };
        }

        const padding = 2;
        const graphWidth = width - padding * 2;
        const graphHeight = height - padding * 2;

        // Normalize values to 0-100 range (fixed scale)
        const minValue = 0;
        const maxValue = 100;
        const range = maxValue - minValue;

        const pointCoords = values.map((value, index) => {
            const x = padding + (index / (values.length - 1 || 1)) * graphWidth;
            // Clamp value between 0 and 100, then normalize to 0-1
            const clampedValue = Math.max(0, Math.min(100, value));
            const normalizedValue = (clampedValue - minValue) / range;
            const y = padding + graphHeight - (normalizedValue * graphHeight);
            return { x, y };
        });

        const points = pointCoords.map(p => `${p.x},${p.y}`).join(' ');

        // Create fill path: start at bottom left, draw curve, line to bottom right, close
        let fillPathStr = '';
        if (fill && pointCoords.length > 0) {
            const firstPoint = pointCoords[0];
            const lastPoint = pointCoords[pointCoords.length - 1];
            const bottomY = padding + graphHeight;
            // Create path: Move to bottom-left, draw line to first point, draw polyline through all points, line to bottom-right, close
            const curvePath = pointCoords.map(p => `L ${p.x},${p.y}`).join(' ');
            fillPathStr = `M ${firstPoint.x},${bottomY} ${curvePath} L ${lastPoint.x},${bottomY} Z`;
        }

        return { points, fillPath: fillPathStr };
    }, [values, width, height, fill]);

    if (values.length === 0) {
        return <Box w={width} h={height} />;
    }

    const fillOpacity = 0.2;

    return (
        <Box flexShrink={0} className="PLOT">
            <svg width={width} height={height} style={{ display: 'block' }}>
                {fill && fillPath && (
                    <path
                        d={fillPath}
                        fill={color}
                        fillOpacity={fillOpacity}
                    />
                )}
                <polyline
                    points={points}
                    fill="none"
                    stroke={color}
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                />
            </svg>
        </Box>
    );
};

const PauseIcon = () => (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
        <rect x="6" y="4" width="4" height="16" />
        <rect x="14" y="4" width="4" height="16" />
    </svg>
);

const PlayIcon = () => (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
        <polygon points="5 3 19 12 5 21" />
    </svg>
);

const TelemetryDisplay = () => {
    const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [isPaused, setIsPaused] = useState(false);
    const [intervalSeconds, setIntervalSeconds] = useState<number>(10);
    const [fillGraphs, setFillGraphs] = useState<boolean>(true);
    const [cpuHistory, setCpuHistory] = useState<number[]>([]);
    const [gpuHistory, setGpuHistory] = useState<number[]>([]); 
    const [gpuMemoryHistory, setGpuMemoryHistory] = useState<number[]>([]);
    const intervalRef = useRef<NodeJS.Timeout | null>(null);
    const abortControllerRef = useRef<AbortController | null>(null);
    const isFetchingRef = useRef<boolean>(false);
    const maxHistoryLength = 60;

    // Memoize fetchTelemetry to prevent recreation on every render
    const fetchTelemetry = useCallback(async () => {
        if (isFetchingRef.current) return;

        // Abort any previous request
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
        }

        // Create new abort controller for this request
        const controller = new AbortController();
        abortControllerRef.current = controller;
        isFetchingRef.current = true;

        try {
            const data = await assaiAPI.getTelemetry(controller.signal);

            if (controller.signal.aborted) return;

            setTelemetry(data);
            setError(null);

            // Update history
            const cpuLoad = data.cpu.load * 100;
            setCpuHistory(prev => {
                const updated = [...prev, cpuLoad];
                return updated.slice(-maxHistoryLength);
            });

            // Get first GPU
            const gpuEntries = Object.entries(data.gpu);
            const firstGpu = gpuEntries.length > 0 ? gpuEntries[0][1] : null;

            if (firstGpu) {
                const gpuLoad = firstGpu.load * 100;
                setGpuHistory(prev => {
                    const updated = [...prev, gpuLoad];
                    return updated.slice(-maxHistoryLength);
                });

                const gpuMemoryPercent = (firstGpu.memory[0] / firstGpu.memory[1]) * 100;
                setGpuMemoryHistory(prev => {
                    const updated = [...prev, gpuMemoryPercent];
                    return updated.slice(-maxHistoryLength);
                });
            }
        } catch (err) {
            if (controller.signal.aborted) return;

            const errorMessage = err instanceof Error ? err.message : 'Failed to load';
            // Don't log abort errors as they're expected when requests are cancelled
            if (!controller.signal.aborted) {
                console.error('Failed to fetch telemetry:', err);
            }
            setError(errorMessage);
            // Don't clear telemetry on error - keep showing last known values
        } finally {
            isFetchingRef.current = false;
            // Clear the abort controller reference if this was the current request
            if (abortControllerRef.current === controller) {
                abortControllerRef.current = null;
            }
        }
    }, [maxHistoryLength]);

    useEffect(() => {
        // Fetch immediately if not paused
        if (!isPaused) {
            fetchTelemetry();
        }

        // Clear existing interval
        if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
        }

        // Set up interval only if not paused
        if (!isPaused) {
            intervalRef.current = setInterval(() => {
                fetchTelemetry();
            }, intervalSeconds * 1000);
        }

        return () => {
            // Synchronously clean up to prevent React Refresh from hanging
            // Abort any pending request immediately
            if (abortControllerRef.current) {
                abortControllerRef.current.abort();
                abortControllerRef.current = null;
            }
            // Clear interval immediately
            if (intervalRef.current) {
                clearInterval(intervalRef.current);
                intervalRef.current = null;
            }
            // Reset fetching flag
            isFetchingRef.current = false;
        };
    }, [isPaused, intervalSeconds, fetchTelemetry]);

    // Memoize GPU calculations to prevent unnecessary recalculations
    // Must be called unconditionally before any early returns (Rules of Hooks)
    const { firstGpu, gpuMemoryPercent, cpuLoadPercent } = useMemo(() => {
        if (!telemetry) {
            return { firstGpu: null, gpuMemoryPercent: 0, cpuLoadPercent: 0 };
        }
        const gpuEntries = Object.entries(telemetry.gpu);
        const firstGpu = gpuEntries.length > 0 ? gpuEntries[0][1] : null;
        const gpuMemoryPercent = firstGpu
            ? (firstGpu.memory[0] / firstGpu.memory[1]) * 100
            : 0;
        const cpuLoadPercent = telemetry.cpu.load * 100;
        return { firstGpu, gpuMemoryPercent, cpuLoadPercent };
    }, [telemetry]);

    // Show error state but keep component visible
    if (error && !telemetry) {
        return (
            <Box
                w="100%"
                pt={3}
                borderTop="1px solid"
                borderColor="gray.700"
            >
                <VStack gap={1.5} align="flex-start">
                    <HStack justify="space-between" w="100%">
                        <Text fontSize="xs" fontWeight="bold" color="gray.500" textTransform="uppercase" letterSpacing="wide">
                            System Stats
                        </Text>
                        <Text fontSize="xs" color="red.400">
                            {error}
                        </Text>
                    </HStack>
                </VStack>
            </Box>
        );
    }

    // If no telemetry data yet, show loading state
    if (!telemetry) {
        return (
            <Box
                w="100%"
                pt={3}
                borderTop="1px solid"
                borderColor="gray.700"
            >
                <VStack gap={1.5} align="flex-start">
                    <HStack justify="space-between" w="100%">
                        <Text fontSize="xs" fontWeight="bold" color="gray.500" textTransform="uppercase" letterSpacing="wide">
                            System Stats
                        </Text>
                        <Text fontSize="xs" color="gray.500">
                            Loading...
                        </Text>
                    </HStack>
                </VStack>
            </Box>
        );
    }

    return (
        <Box
            w="100%"
            pt={3}
            borderTop="1px solid"
            borderColor="gray.700"
        >
            <VStack gap={1.5} align="flex-start">
                <HStack justify="space-between" w="100%">
                    <HStack gap={2}>
                        <Text fontSize="xs" fontWeight="bold" color="gray.500" textTransform="uppercase" letterSpacing="wide">
                            System Stats
                        </Text>
                        {error && (
                            <Text fontSize="xs" color="orange.400" title={error}>
                                ⚠
                            </Text>
                        )}
                    </HStack>
                    <HStack gap={1}>
                        <Input
                            type="number"
                            size="xs"
                            value={intervalSeconds}
                            onChange={(e) => {
                                const numValue = parseInt(e.target.value) || 10;
                                if (numValue >= 1 && numValue <= 300) {
                                    setIntervalSeconds(numValue);
                                }
                            }}
                            min={1}
                            max={300}
                            w="50px"
                            h="20px"
                            px={1}
                            fontSize="xs"
                            textAlign="center"
                            borderColor="gray.600"
                            _focus={{ borderColor: 'gray.500' }}
                        />
                        <Text fontSize="xs" color="gray.500" w="15px">
                            s
                        </Text>
                        {/* <IconButton
                            aria-label={fillGraphs ? 'Disable fill' : 'Enable fill'}
                            size="xs"
                            variant={fillGraphs ? "solid" : "ghost"}
                            colorScheme="gray"
                            onClick={() => setFillGraphs(!fillGraphs)}
                            title={fillGraphs ? 'Disable area fill' : 'Enable area fill'}
                        >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                            </svg>
                        </IconButton> */}
                        <IconButton
                            aria-label={isPaused ? 'Resume telemetry' : 'Pause telemetry'}
                            size="xs"
                            variant="ghost"
                            colorScheme="gray"
                            onClick={() => setIsPaused(!isPaused)}
                        >
                            {isPaused ? <PlayIcon /> : <PauseIcon />}
                        </IconButton>
                    </HStack>
                </HStack>
                <VStack gap={1.5} align="stretch" w="100%">
                    <HStack justify="space-between" fontSize="xs" align="center">
                        <Text color="gray.400" w="57px" fontWeight="medium">
                            CPU:
                        </Text>
                        <Sparkline values={cpuHistory} color="#90cdf4" fill={fillGraphs} />
                        <Text color="blue.300" w="40px" fontFamily="mono">
                            {cpuLoadPercent.toFixed(1)}%
                        </Text>
                    </HStack>
                    {firstGpu && (
                        <>
                            <HStack justify="space-between" fontSize="xs" align="center">
                                <Text color="gray.400" w="57px" fontWeight="medium">
                                    GPU:
                                </Text>
                                <Sparkline values={gpuHistory} color="#c084fc" fill={fillGraphs} />
                                <Text color="purple.300" w="40px" fontFamily="mono">
                                    {(firstGpu.load * 100).toFixed(1)}%
                                </Text>
                            </HStack>
                            <HStack justify="space-between" fontSize="xs" align="center">
                                <Text color="gray.400" w="57px" fontWeight="medium">
                                    GPU Mem:
                                </Text>
                                <Sparkline values={gpuMemoryHistory} color="#86efac" fill={fillGraphs} />
                                <Text color="green.300" w="40px" fontFamily="mono">
                                    {gpuMemoryPercent.toFixed(1)}%
                                </Text>
                            </HStack>
                        </>
                    )}
                </VStack>
            </VStack>
        </Box>
    );
};

export default TelemetryDisplay;

