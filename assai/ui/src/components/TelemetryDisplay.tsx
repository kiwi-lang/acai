import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { Box, HStack, Text, VStack, IconButton, Input } from '@chakra-ui/react';
import { Tooltip } from './ui/tooltip';
import { useAgentSocket } from '../contexts/WebSocketContext';

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
    network?: {
        bytes_recv: number;
        bytes_sent: number;
        packets_recv: number;
        packets_sent: number;
        errin: number;
        errout: number;
        dropin: number;
        dropout: number;
    };
    disk?: {
        busy_time: number;
        read_bytes: number;
        read_count: number;
        read_time: number;
        write_count: number;
        write_time: number;
    };
}

interface SparklineProps {
    values: number[];
    color: string;
    width?: number;
    height?: number;
    fill?: boolean;
}

interface BidirectionalSparklineProps {
    uploadValues: number[];
    downloadValues: number[];
    uploadColor: string;
    downloadColor: string;
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

const BidirectionalSparkline = ({
    uploadValues,
    downloadValues,
    uploadColor,
    downloadColor,
    width = 60,
    height = 20,
    fill = false
}: BidirectionalSparklineProps) => {
    const { uploadPoints, downloadPoints, uploadFillPath, downloadFillPath, centerY } = useMemo(() => {
        if (uploadValues.length === 0 && downloadValues.length === 0) {
            return { uploadPoints: '', downloadPoints: '', uploadFillPath: '', downloadFillPath: '', centerY: height / 2 };
        }

        const padding = 2;
        const graphWidth = width - padding * 2;
        const graphHeight = height - padding * 2;
        const centerYPos = padding + graphHeight / 2;

        // Find separate maximum absolute values for upload and download to scale independently
        const uploadMaxAbs = Math.max(...uploadValues.map(v => Math.abs(v)), 0.01);
        const downloadMaxAbs = Math.max(...downloadValues.map(v => Math.abs(v)), 0.01);

        // Create point coordinates: upload above center (positive), download below center (negative)
        const maxLength = Math.max(uploadValues.length, downloadValues.length);
        const uploadPointCoords: { x: number; y: number }[] = [];
        const downloadPointCoords: { x: number; y: number }[] = [];

        for (let i = 0; i < maxLength; i++) {
            const x = padding + (i / (maxLength - 1 || 1)) * graphWidth;

            // Upload: positive values above center (uses its own scale)
            const uploadValue = uploadValues[i] || 0;
            const uploadNormalized = uploadMaxAbs > 0 ? uploadValue / uploadMaxAbs : 0;
            const uploadY = centerYPos - (uploadNormalized * (graphHeight / 2));
            uploadPointCoords.push({ x, y: uploadY });

            // Download: negative values below center (uses its own scale)
            const downloadValue = downloadValues[i] || 0;
            const downloadNormalized = downloadMaxAbs > 0 ? downloadValue / downloadMaxAbs : 0;
            const downloadY = centerYPos + (downloadNormalized * (graphHeight / 2));
            downloadPointCoords.push({ x, y: downloadY });
        }

        const uploadPoints = uploadPointCoords.map(p => `${p.x},${p.y}`).join(' ');
        const downloadPoints = downloadPointCoords.map(p => `${p.x},${p.y}`).join(' ');

        // Create fill paths: fill from center line to the data line
        let uploadFillPathStr = '';
        let downloadFillPathStr = '';

        if (fill) {
            if (uploadPointCoords.length > 0) {
                const firstPoint = uploadPointCoords[0];
                const lastPoint = uploadPointCoords[uploadPointCoords.length - 1];
                const curvePath = uploadPointCoords.map(p => `L ${p.x},${p.y}`).join(' ');
                uploadFillPathStr = `M ${firstPoint.x},${centerYPos} ${curvePath} L ${lastPoint.x},${centerYPos} Z`;
            }

            if (downloadPointCoords.length > 0) {
                const firstPoint = downloadPointCoords[0];
                const lastPoint = downloadPointCoords[downloadPointCoords.length - 1];
                const curvePath = downloadPointCoords.map(p => `L ${p.x},${p.y}`).join(' ');
                downloadFillPathStr = `M ${firstPoint.x},${centerYPos} ${curvePath} L ${lastPoint.x},${centerYPos} Z`;
            }
        }

        return {
            uploadPoints,
            downloadPoints,
            uploadFillPath: uploadFillPathStr,
            downloadFillPath: downloadFillPathStr,
            centerY: centerYPos
        };
    }, [uploadValues, downloadValues, width, height, fill]);

    if (uploadValues.length === 0 && downloadValues.length === 0) {
        return <Box w={width} h={height} />;
    }

    const fillOpacity = 0.2;

    return (
        <Box flexShrink={0} className="PLOT">
            <svg width={width} height={height} style={{ display: 'block' }}>
                {/* Center line */}
                <line
                    x1={2}
                    y1={centerY}
                    x2={width - 2}
                    y2={centerY}
                    stroke="gray"
                    strokeWidth="0.5"
                    strokeOpacity="0.3"
                />
                {/* Upload fill (above center) */}
                {fill && uploadFillPath && (
                    <path
                        d={uploadFillPath}
                        fill={uploadColor}
                        fillOpacity={fillOpacity}
                    />
                )}
                {/* Download fill (below center) */}
                {fill && downloadFillPath && (
                    <path
                        d={downloadFillPath}
                        fill={downloadColor}
                        fillOpacity={fillOpacity}
                    />
                )}
                {/* Upload line */}
                {uploadPoints && (
                    <polyline
                        points={uploadPoints}
                        fill="none"
                        stroke={uploadColor}
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                    />
                )}
                {/* Download line */}
                {downloadPoints && (
                    <polyline
                        points={downloadPoints}
                        fill="none"
                        stroke={downloadColor}
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                    />
                )}
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
    const { socket, isConnected } = useAgentSocket();
    const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [isPaused, setIsPaused] = useState(false);
    const [intervalSeconds, setIntervalSeconds] = useState<number>(1);
    const [fillGraphs, setFillGraphs] = useState<boolean>(true);
    const [cpuHistory, setCpuHistory] = useState<number[]>([]);
    const [gpuHistory, setGpuHistory] = useState<number[]>([]);
    const [gpuMemoryHistory, setGpuMemoryHistory] = useState<number[]>([]);
    const [networkUploadHistory, setNetworkUploadHistory] = useState<number[]>([]);
    const [networkDownloadHistory, setNetworkDownloadHistory] = useState<number[]>([]);
    const [diskReadTimeHistory, setDiskReadTimeHistory] = useState<number[]>([]);
    const [diskWriteTimeHistory, setDiskWriteTimeHistory] = useState<number[]>([]);
    const [sparklineWidth, setSparklineWidth] = useState<number>(200);
    const intervalRef = useRef<NodeJS.Timeout | null>(null);
    const isFetchingRef = useRef<boolean>(false);
    const prevNetworkRef = useRef<{ bytes_sent: number; bytes_recv: number; time: number } | null>(null);
    const prevDiskRef = useRef<{ read_time: number; write_time: number; time: number } | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const maxHistoryLength = 60;

    // Process telemetry data and update state
    const processTelemetryData = useCallback((data: TelemetryData) => {
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

        // Update network history
        if (data.network) {
            const currentTime = Date.now() / 1000; // Convert to seconds
            const prev = prevNetworkRef.current;

            if (prev) {
                const timeDelta = currentTime - prev.time;
                if (timeDelta > 0) {
                    // Calculate rates in bytes per second
                    const uploadRate = (data.network.bytes_sent - prev.bytes_sent) / timeDelta;
                    const downloadRate = (data.network.bytes_recv - prev.bytes_recv) / timeDelta;

                    // Convert to MB/s for display (but store as bytes/s for consistency)
                    setNetworkUploadHistory(prevHistory => {
                        const updated = [...prevHistory, uploadRate / (1024 * 1024)]; // MB/s
                        return updated.slice(-maxHistoryLength);
                    });
                    setNetworkDownloadHistory(prevHistory => {
                        const updated = [...prevHistory, downloadRate / (1024 * 1024)]; // MB/s
                        return updated.slice(-maxHistoryLength);
                    });
                }
            }

            prevNetworkRef.current = {
                bytes_sent: data.network.bytes_sent,
                bytes_recv: data.network.bytes_recv,
                time: currentTime
            };
        }

        // Update disk history
        if (data.disk) {
            const currentTime = Date.now() / 1000; // Convert to seconds
            const prev = prevDiskRef.current;

            // Calculate read and write time rates
            if (prev) {
                const timeDelta = currentTime - prev.time;
                if (timeDelta > 0) {
                    // Calculate rates: (current - previous) / time_delta
                    const readTimeRate = (data.disk.read_time - prev.read_time) / timeDelta;
                    const writeTimeRate = (data.disk.write_time - prev.write_time) / timeDelta;

                    setDiskReadTimeHistory(prevHistory => {
                        const updated = [...prevHistory, readTimeRate];
                        return updated.slice(-maxHistoryLength);
                    });
                    setDiskWriteTimeHistory(prevHistory => {
                        const updated = [...prevHistory, writeTimeRate];
                        return updated.slice(-maxHistoryLength);
                    });
                }
            }

            prevDiskRef.current = {
                read_time: data.disk.read_time,
                write_time: data.disk.write_time,
                time: currentTime
            };
        }
    }, [maxHistoryLength]);

    // Memoize fetchTelemetry to prevent recreation on every render
    const fetchTelemetry = useCallback(() => {
        if (isFetchingRef.current || !socket || !isConnected) return;

        isFetchingRef.current = true;
        // Request telemetry via websocket
        socket.emit('request_telemetry');
    }, [socket, isConnected]);

    // Set up websocket listeners for telemetry
    useEffect(() => {
        if (!socket || !isConnected) {
            return;
        }

        // Handle telemetry data received via websocket
        const handleTelemetry = (data: TelemetryData) => {
            isFetchingRef.current = false;
            processTelemetryData(data);
        };

        // Handle telemetry errors received via websocket
        const handleTelemetryError = (errorData: { error: string }) => {
            isFetchingRef.current = false;
            const errorMessage = errorData.error || 'Failed to load telemetry';
            console.error('Failed to fetch telemetry:', errorMessage);
            setError(errorMessage);
            // Don't clear telemetry on error - keep showing last known values
        };

        // Register websocket listeners
        socket.on('telemetry', handleTelemetry);
        socket.on('telemetry_error', handleTelemetryError);

        return () => {
            socket.off('telemetry', handleTelemetry);
            socket.off('telemetry_error', handleTelemetryError);
        };
    }, [socket, isConnected, processTelemetryData]);

    useEffect(() => {
        // Only proceed if websocket is connected
        if (!isConnected || !socket) {
            return;
        }

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
            // Clear interval immediately
            if (intervalRef.current) {
                clearInterval(intervalRef.current);
                intervalRef.current = null;
            }
            // Reset fetching flag
            isFetchingRef.current = false;
        };
    }, [isPaused, intervalSeconds, fetchTelemetry, isConnected, socket]);

    // Update sparkline width based on container size
    useEffect(() => {
        const updateWidth = () => {
            if (containerRef.current) {
                const containerWidth = containerRef.current.offsetWidth;
                // Reserve space for label (57px) and value (40px) + padding
                const availableWidth = containerWidth - 57 - 40 - 20; // 20px for gaps/padding
                setSparklineWidth(Math.max(120, availableWidth)); // Minimum 150px
            }
        };

        // Use a small delay to ensure DOM is ready
        const timeoutId = setTimeout(updateWidth, 0);
        window.addEventListener('resize', updateWidth);
        return () => {
            clearTimeout(timeoutId);
            window.removeEventListener('resize', updateWidth);
        };
    }, [telemetry]);

    // Memoize GPU calculations to prevent unnecessary recalculations
    // Must be called unconditionally before any early returns (Rules of Hooks)
    const { firstGpu, gpuMemoryPercent, cpuLoadPercent, networkUploadRate, networkDownloadRate, diskReadTimeRate, diskWriteTimeRate } = useMemo(() => {
        if (!telemetry) {
            return { firstGpu: null, gpuMemoryPercent: 0, cpuLoadPercent: 0, networkUploadRate: 0, networkDownloadRate: 0, diskReadTimeRate: 0, diskWriteTimeRate: 0 };
        }
        const gpuEntries = Object.entries(telemetry.gpu);
        const firstGpu = gpuEntries.length > 0 ? gpuEntries[0][1] : null;
        const gpuMemoryPercent = firstGpu
            ? (firstGpu.memory[0] / firstGpu.memory[1]) * 100
            : 0;
        const cpuLoadPercent = telemetry.cpu.load * 100;

        // Get current network rates from history (last value)
        const networkUploadRate = networkUploadHistory.length > 0
            ? networkUploadHistory[networkUploadHistory.length - 1]
            : 0;
        const networkDownloadRate = networkDownloadHistory.length > 0
            ? networkDownloadHistory[networkDownloadHistory.length - 1]
            : 0;

        // Get current disk read/write time rates from history (last value)
        const diskReadTimeRate = diskReadTimeHistory.length > 0
            ? diskReadTimeHistory[diskReadTimeHistory.length - 1]
            : 0;
        const diskWriteTimeRate = diskWriteTimeHistory.length > 0
            ? diskWriteTimeHistory[diskWriteTimeHistory.length - 1]
            : 0;

        return { firstGpu, gpuMemoryPercent, cpuLoadPercent, networkUploadRate, networkDownloadRate, diskReadTimeRate, diskWriteTimeRate };
    }, [telemetry, networkUploadHistory, networkDownloadHistory, diskReadTimeHistory, diskWriteTimeHistory]);

    // Show error state but keep component visible
    if (error && !telemetry) {
        return (
            <Box
                w="100%"
                pt={3}
                borderTop="1px solid"
                borderColor="var(--border-primary)"
            >
                <VStack gap={1.5} align="flex-start">
                    <HStack justify="space-between" w="100%">
                        <Text fontSize="xs" fontWeight="bold" color="var(--text-muted)" textTransform="uppercase" letterSpacing="wide">
                            System Stats
                        </Text>
                        <Text fontSize="xs" color="var(--text-error)">
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
                borderColor="var(--border-primary)"
            >
                <VStack gap={1.5} align="flex-start">
                    <HStack justify="space-between" w="100%">
                        <Text fontSize="xs" fontWeight="bold" color="var(--text-muted)" textTransform="uppercase" letterSpacing="wide">
                            System Stats
                        </Text>
                        <Text fontSize="xs" color="var(--text-muted)">
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
            borderColor="var(--border-primary)"
        >
            <VStack gap={1.5} align="flex-start">
                <HStack justify="space-between" w="100%">
                    <HStack gap={2}>
                        <Text fontSize="xs" fontWeight="bold" color="var(--text-muted)" textTransform="uppercase" letterSpacing="wide">
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
                            borderColor="var(--border-secondary)"
                            _focus={{ borderColor: 'var(--border-secondary)' }}
                        />
                        <Text fontSize="xs" color="var(--text-muted)" w="15px">
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
                <Box ref={containerRef} w="100%">
                    <VStack gap={1.5} align="stretch" w="100%">
                        <HStack justify="space-between" fontSize="xs" align="center" w="100%">
                            <Text color="var(--text-tertiary)" w="57px" fontWeight="medium" flexShrink={0}>
                                CPU:
                            </Text>
                            <Box flex={1} display="flex" justifyContent="center">
                                <Sparkline values={cpuHistory} color="var(--spark-cpu)" fill={fillGraphs} width={sparklineWidth} />
                            </Box>
                            <Text color="var(--stat-cpu)" w="40px" fontFamily="mono" textAlign="right" flexShrink={0}>
                                {cpuLoadPercent.toFixed(1)}%
                            </Text>
                        </HStack>
                        {firstGpu && (
                            <>
                                <HStack justify="space-between" fontSize="xs" align="center" w="100%">
                                    <Text color="var(--text-tertiary)" w="57px" fontWeight="medium" flexShrink={0}>
                                        GPU:
                                    </Text>
                                    <Box flex={1} display="flex" justifyContent="center">
                                        <Sparkline values={gpuHistory} color="var(--spark-gpu)" fill={fillGraphs} width={sparklineWidth} />
                                    </Box>
                                    <Text color="var(--stat-gpu)" w="40px" fontFamily="mono" textAlign="right" flexShrink={0}>
                                        {(firstGpu.load * 100).toFixed(1)}%
                                    </Text>
                                </HStack>
                                <HStack justify="space-between" fontSize="xs" align="center" w="100%">
                                    <Text color="var(--text-tertiary)" w="57px" fontWeight="medium" flexShrink={0}>
                                        GPU Mem:
                                    </Text>
                                    <Box flex={1} display="flex" justifyContent="center">
                                        <Sparkline values={gpuMemoryHistory} color="var(--spark-gpu-mem)" fill={fillGraphs} width={sparklineWidth} />
                                    </Box>
                                    <Text color="var(--stat-gpu-mem)" w="40px" fontFamily="mono" textAlign="right" flexShrink={0}>
                                        {gpuMemoryPercent.toFixed(1)}%
                                    </Text>
                                </HStack>
                            </>
                        )}
                        {telemetry.network && (
                            <HStack justify="space-between" fontSize="xs" align="center" w="100%">
                                <Text color="var(--text-tertiary)" w="57px" fontWeight="medium" flexShrink={0}>
                                    Network:
                                </Text>
                                <Box flex={1} display="flex" justifyContent="center">
                                    <BidirectionalSparkline
                                        uploadValues={networkUploadHistory}
                                        downloadValues={networkDownloadHistory}
                                        uploadColor="var(--spark-net-up)"
                                        downloadColor="var(--spark-net-down)"
                                        fill={fillGraphs}
                                        width={sparklineWidth}
                                    />
                                </Box>
                                <Tooltip
                                    content="MB/s"
                                    openDelay={100}
                                    closeDelay={100}
                                >
                                    <Box as="span" cursor="help">
                                        <VStack gap={0} align="flex-end" flexShrink={0} w="40px">
                                            <Text color="var(--stat-net-up)" fontFamily="mono" textAlign="right" fontSize="xs" lineHeight="1">
                                                ↑{networkUploadRate.toFixed(2)}
                                            </Text>
                                            <Text color="var(--stat-net-down)" fontFamily="mono" textAlign="right" fontSize="xs" lineHeight="1">
                                                ↓{networkDownloadRate.toFixed(2)}
                                            </Text>
                                        </VStack>
                                    </Box>
                                </Tooltip>
                            </HStack>
                        )}
                        {telemetry.disk && (
                            <HStack justify="space-between" fontSize="xs" align="center" w="100%">
                                <Text color="var(--text-tertiary)" w="57px" fontWeight="medium" flexShrink={0}>
                                    Disk R/W:
                                </Text>
                                <Box flex={1} display="flex" justifyContent="center">
                                    <BidirectionalSparkline
                                        uploadValues={diskReadTimeHistory}
                                        downloadValues={diskWriteTimeHistory}
                                        uploadColor="var(--spark-disk-read)"
                                        downloadColor="var(--spark-disk-write)"
                                        fill={fillGraphs}
                                        width={sparklineWidth}
                                    />
                                </Box>
                                <Tooltip
                                    content="ms/s"
                                    openDelay={100}
                                    closeDelay={100}
                                >
                                    <Box as="span" cursor="help">
                                        <VStack gap={0} align="flex-end" flexShrink={0} w="40px">
                                            <Text color="var(--stat-disk-read)" fontFamily="mono" textAlign="right" fontSize="xs" lineHeight="1">
                                                {diskReadTimeRate.toFixed(1)}R
                                            </Text>
                                            <Text color="var(--stat-disk-write)" fontFamily="mono" textAlign="right" fontSize="xs" lineHeight="1">
                                                {diskWriteTimeRate.toFixed(1)}W
                                            </Text>
                                        </VStack>
                                    </Box>
                                </Tooltip>
                            </HStack>
                        )}
                    </VStack>
                </Box>
            </VStack>
        </Box>
    );
};

export default TelemetryDisplay;

