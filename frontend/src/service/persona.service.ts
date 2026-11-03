import { activatePersona, listPersonas } from "../api/endpoints";
import type { PersonasResponse } from "../api/types";
import { getApiClient } from "./api-client";
import { notifyPetSettingsChanged } from "./settings.service";

export function loadPersonas(): Promise<PersonasResponse> {
  return getApiClient().then((client) => listPersonas(client));
}

export async function switchPersona(personaId: string): Promise<PersonasResponse> {
  const client = await getApiClient();
  const next = await activatePersona(client, personaId);
  // 人设决定形象组：切换成功后让悬浮窗重新拉取目录并重载形象。
  await notifyPetSettingsChanged("persona");
  return next;
}
