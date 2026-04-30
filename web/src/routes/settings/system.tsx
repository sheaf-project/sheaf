import { SystemProfileCard } from "@/components/settings/system-profile-card";
import { TagsManagerCard } from "@/components/settings/tags-manager-card";
import { CustomFieldsCard } from "@/components/settings/custom-fields-card";
import { FrontPreferencesCard } from "@/components/settings/front-preferences-card";

export function SettingsSystemPage() {
  return (
    <>
      <SystemProfileCard />
      <TagsManagerCard />
      <CustomFieldsCard />
      <FrontPreferencesCard />
    </>
  );
}
