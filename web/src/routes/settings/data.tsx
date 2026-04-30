import { StorageUsageCard } from "@/components/settings/storage-usage-card";
import { UploadedFilesCard } from "@/components/settings/uploaded-files-card";
import { DataExportCard } from "@/components/settings/data-export-card";
import { DataImportCard } from "@/components/settings/data-import-card";

export function SettingsDataPage() {
  return (
    <>
      <StorageUsageCard />
      <UploadedFilesCard />
      <DataExportCard />
      <DataImportCard />
    </>
  );
}
