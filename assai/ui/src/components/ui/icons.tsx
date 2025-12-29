/**
 * Centralized icon exports for the application
 *
 * This file contains all icons used throughout the website.
 * Import icons from this file instead of directly from react-icons.
 *
 * Usage:
 *   import { IconPlus, IconEye, IconTrash } from '../ui/icons';
 */

// Action icons
export { LuPlus as IconPlus } from 'react-icons/lu';
export { LuTrash2 as IconTrash } from 'react-icons/lu';
export { LuRefreshCw as IconRefresh } from 'react-icons/lu';
export { LuCopy as IconCopy } from 'react-icons/lu';
export { LuDownload as IconDownload } from 'react-icons/lu';
export { LuExternalLink as IconExternalLink } from 'react-icons/lu';
export { LuSearch as IconSearch } from 'react-icons/lu';

// Navigation icons
export { LuArrowLeft as IconArrowLeft } from 'react-icons/lu';
export { LuX as IconX } from 'react-icons/lu';

// Status/Info icons
export { LuInfo as IconInfo } from 'react-icons/lu';
export { LuCircleCheck as IconCircleCheck } from 'react-icons/lu';
export { LuTriangleAlert as IconTriangleAlert } from 'react-icons/lu';
export { LuClock as IconClock } from 'react-icons/lu';
export { LuEye as IconEye } from 'react-icons/lu';

// Theme icons
export { LuMoon as IconMoon } from 'react-icons/lu';
export { LuSun as IconSun } from 'react-icons/lu';

// Re-export all icons with their original names for backward compatibility
// This allows gradual migration from direct imports
export {
    LuPlus,
    LuTrash2,
    LuRefreshCw,
    LuCopy,
    LuDownload,
    LuExternalLink,
    LuSearch,
    LuArrowLeft,
    LuX,
    LuInfo,
    LuCircleCheck,
    LuTriangleAlert,
    LuClock,
    LuEye,
    LuMoon,
    LuSun,
} from 'react-icons/lu';
