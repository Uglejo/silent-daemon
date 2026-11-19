  __grade404(THREE, root, 1, 1);
  const flat = new THREE.Box3().setFromObject(root, true).getSize(new THREE.Vector3());
  if (flat.y < 0.5 * Math.max(flat.x, flat.z)) root.rotation.x = 15 * Math.PI / 180;
  __bake404(THREE, root);
