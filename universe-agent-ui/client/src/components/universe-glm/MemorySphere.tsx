"use client";

import { useEffect, useRef } from "react";
import { Group } from "three";
import type { UniverseData } from "@/lib/universe/generate";
import { Agents } from "./Agents";
import { CosmicCore } from "./CosmicCore";
import { MemoryStars } from "./MemoryStars";
import { shared } from "./shared";
import { SpiralArms } from "./SpiralArms";
import { Systems } from "./Systems";

/**
 * The memory sphere, embedded edition (MiniUniverse / ThinkingSpark): the
 * visible mind of Universe Agent — arms + stars + systems + core + agents
 * inside one rotating group, at a fraction of the full-page particle cost.
 * The heavy decorative layers (lattice, nebulae, dark cores, background
 * shell, boundary fresnel) belonged to the deleted full-page /universe view;
 * the embedded canvas was always background-free by design.
 */
export function MemorySphere({
  data,
  embedded = false,
}: {
  data: UniverseData;
  embedded?: boolean;
}) {
  const groupRef = useRef<Group>(null);

  useEffect(() => {
    shared.sphere = groupRef.current;
    return () => {
      shared.sphere = null;
    };
  }, []);

  void embedded; // one mode now; the prop stays for call-site compatibility
  return (
    <group ref={groupRef} rotation={[0, 0.65, 0.015]}>
      <SpiralArms data={data} />
      <MemoryStars data={data} />
      <Systems data={data} />
      <CosmicCore data={data} />
      <Agents data={data} />
    </group>
  );
}
