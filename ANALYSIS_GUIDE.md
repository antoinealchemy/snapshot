# Guide d'Analyse des Corrélations

Ce document recense les biais connus et les précautions à prendre lors de l'analyse des données de snapshots pour améliorer le winrate du bot.

---

## Biais Connus

### curve_percentage

**Problème** : Non comparable entre plateformes.

| Plateforme | MC de graduation | Comportement |
|------------|------------------|--------------|
| pump.fun | ~$69K (historique) | Fixe |
| PumpSwap | $83K à $135K observés | Variable |
| letsbonk | Variable | Données insuffisantes |

**Interprétation correcte** :
- Utiliser uniquement en relatif : early (<30%) vs late (>70%)
- **Toujours segmenter par plateforme** avant comparaison
- Ne JAMAIS comparer un 30% pump.fun avec un 30% PumpSwap

**Exemple de mauvaise analyse** :
> "Les tokens avec curve_percentage < 30% ont un meilleur winrate"

**Exemple de bonne analyse** :
> "Sur pump.fun uniquement, les tokens avec curve_percentage < 30% ont un meilleur winrate de X% vs Y% pour >70%"

---

### detection_type = APPROXIMATION

**Contexte** : Tokens avec `ath_ratio >= 0.5` au call mais sans nouvel ATH détecté à J+7.

**Problème** : Le `true_multiple` affiché est une **borne BASSE** (sous-estimation).

```
Scénario réel possible :
- Token callé à MC=$30K, ATH=$60K
- J+2 : Pump à MC=$90K (x3!) puis dump
- J+7 : MC=$25K, ATH toujours $90K > $60K → ATH_CONFIRMED

Mais si :
- J+2 : Pump à MC=$55K (x1.8) puis dump
- J+7 : MC=$25K, ATH=$60K (inchangé) → APPROXIMATION
- Multiple affiché : x0.83 (sous-estimé, le vrai max était x1.8)
```

**Impact** : Le winrate réel est potentiellement **supérieur** au winrate mesuré.

**Recommandation** :
- Surveiller le ratio ATH_CONFIRMED vs APPROXIMATION
- Si >60% ATH_CONFIRMED → données fiables
- Si <40% ATH_CONFIRMED → sous-estimation probable du winrate

---

### Tokens exclus (ath_ratio < 0.5)

**Règle** : Tokens avec `excluded_from_analysis = 1` sont stockés en DB mais hors analyse principale.

**Biais de survivant** :
- Ces tokens sont déjà en dump au moment du call
- Les rares succès dans cette zone partagent probablement leurs patterns avec les échecs massifs
- Population trop biaisée pour en tirer des conclusions

**Recommandation** :
- Ne PAS inclure dans l'analyse principale
- Si analyse séparée souhaitée, l'indiquer clairement
- Minimum 100 tokens exclus avant toute tentative d'analyse

---

### Volume faible sur certaines tranches

| Tranche | Seuil minimum | Action |
|---------|---------------|--------|
| Horaire (4h) | < 20 calls | Résultats non significatifs |
| Jour de semaine | < 4 semaines de données | Attendre plus de données |
| Prix SOL | Tranches vides si SOL stable | Normal, ignorer |
| Wallet | < 3 calls | Non affiché dans le rapport |

**Règle générale** : Ne tirer des conclusions que sur des groupes avec **minimum 30 calls**.

En dessous : noter comme "tendance à confirmer" uniquement.

---

## Variables par Ordre d'Importance Estimée

| Rang | Variable | Impact | Données de référence |
|------|----------|--------|---------------------|
| 1 | `wallet_name` | **Démontré** | SIRIUS 80% vs Andromède 14% |
| 2 | `hour_utc` | Probable | US awake = plus de volume/liquidité |
| 3 | `day_of_week` | Probable | Weekend = moins de volume Solana |
| 4 | `api_mc_usd` | Probable | Zone 20-40K semble meilleure |
| 5 | `sol_price_at_signal` | Possible | Impact sur MC de graduation |
| 6 | `platform` | À vérifier | pump.fun vs PumpSwap vs autres |
| 7 | `risk_score` | À vérifier | 0 = safe, 10000 = danger |
| 8 | `holders` | À vérifier | Plus = plus distribué |
| 9 | `curve_percentage` | **Biais important** | Voir section dédiée |
| 10 | `buyers_5m / sellers_5m` | À vérifier | Ratio buy/sell momentum |

---

## Hypothèses à Tester

### Timing

- [ ] Les calls entre **00h-04h UTC** ont-ils un moins bon winrate ?
  - Hypothèse : Peu de traders actifs = moins de momentum

- [ ] Les calls le **weekend** ont-ils un winrate différent ?
  - Hypothèse : Volume Solana réduit = moins de liquidité

- [ ] Y a-t-il une **heure optimale par wallet** ?
  - Hypothèse : Certains wallets actifs la nuit = signal de desperation ?

### Plateforme

- [ ] **pump.fun vs PumpSwap** : différence de winrate ?
  - Hypothèse : pump.fun plus liquide = meilleur

- [ ] Les tokens **letsbonk** performent-ils différemment ?
  - Données probablement insuffisantes

### Marché

- [ ] **SOL > $100** = meilleur winrate ?
  - Hypothèse : Marché bull = momentum plus facile

- [ ] Corrélation entre **volatilité SOL** et winrate ?
  - Nécessite données historiques supplémentaires

### Risk Metrics

- [ ] Les tokens avec **risk_score = 0** performent-ils mieux ?
  - Hypothèse : Moins de red flags = plus safe

- [ ] **risk_top10 < 20%** = meilleur winrate ?
  - Hypothèse : Distribution plus saine

- [ ] Impact de **lp_burn = 1** ?
  - Hypothèse : LP burned = plus de confiance

### Volume/Momentum

- [ ] Ratio **buyers_5m / sellers_5m > 1.5** = signal positif ?
  - Hypothèse : Momentum acheteur = continuation

- [ ] **volume_5m_usd** minimum pour bon winrate ?
  - Hypothèse : Volume = liquidité = moins de slippage

---

## Requêtes SQL Utiles

### Winrate par tranche de MC

```sql
SELECT
    CASE
        WHEN api_mc_usd < 20000 THEN '0-20K'
        WHEN api_mc_usd < 40000 THEN '20-40K'
        WHEN api_mc_usd < 100000 THEN '40-100K'
        ELSE '100K+'
    END as mc_range,
    COUNT(*) as total,
    SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) as x2,
    ROUND(SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as winrate
FROM token_snapshots
WHERE checked_j7 IS NOT NULL AND excluded_from_analysis = 0
GROUP BY mc_range
ORDER BY MIN(api_mc_usd);
```

### Corrélation hour_utc × wallet_name

```sql
SELECT
    wallet_name,
    (hour_utc / 4) * 4 as hour_range,
    COUNT(*) as calls,
    ROUND(SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as winrate
FROM token_snapshots
WHERE checked_j7 IS NOT NULL AND excluded_from_analysis = 0
GROUP BY wallet_name, hour_range
HAVING calls >= 5
ORDER BY wallet_name, hour_range;
```

### Detection type par wallet

```sql
SELECT
    wallet_name,
    detection_type,
    COUNT(*) as count,
    ROUND(SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as winrate
FROM token_snapshots
WHERE checked_j7 IS NOT NULL AND excluded_from_analysis = 0
GROUP BY wallet_name, detection_type
ORDER BY wallet_name, detection_type;
```

---

## Seuils de Significativité

| Niveau de confiance | Minimum calls | Usage |
|---------------------|---------------|-------|
| **Tendance** | 10-29 | "Semble indiquer que..." |
| **Indicatif** | 30-99 | "Les données suggèrent..." |
| **Fiable** | 100-499 | "L'analyse montre..." |
| **Robuste** | 500+ | "Il est établi que..." |

---

## Checklist Avant Conclusions

- [ ] Le groupe analysé a-t-il **>= 30 calls** ?
- [ ] Les tokens **APPROXIMATION** représentent-ils < 60% du groupe ?
- [ ] La variable analysée est-elle **segmentée par plateforme** si pertinent ?
- [ ] Les **tokens exclus** sont-ils bien exclus de l'analyse ?
- [ ] Le **biais de curve_percentage** est-il pris en compte ?
- [ ] Les **tranches horaires/jours** ont-elles assez de volume ?

---

## Notes de Version

- **v1.0** (2026-02-21) : Document initial
- Variables identifiées depuis analyse des données wallets 7j
- Hypothèses basées sur observations préliminaires
