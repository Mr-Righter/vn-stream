// Public models come from the server contract. Keep feature-specific pagination
// and summary unions here, where consumers share them.
import type { AssetDirectory, AssetEntry, SaveVariables as SaveVariablePage } from './generated/types.gen.js';

export type {
  ArchiveUpload,
  AssetEntry,
  AssetPreparation,
  AssetSummary,
  AssetTask,
  Capabilities as ProjectCapabilities,
  DeletionTask as ProjectDeletion,
  DirectoryFile,
  DirectoryUpload as DirectoryUploadRecord,
  Engine,
  ImportSources,
  ImportTask,
  MigrationEntry,
  MigrationProject,
  MigrationResult,
  MigrationReview as MigrationPreview,
  MigrationSource,
  MigrationTask,
  Project,
  RuntimeOption as ProjectRuntime,
  SaveBackup,
  SaveEntry,
  SaveField,
  SaveListing,
  SigningState,
} from './generated/types.gen.js';

export type AssetListEntry = AssetDirectory | AssetEntry;
export type SaveVariables = SaveVariablePage & { total: number };
