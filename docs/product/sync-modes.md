# Modes de synchronisation

IMTégrale propose trois niveaux de continuité. Le choix appartient au
propriétaire du compte et le mode initial reste toujours **À la demande**.
L'interface n'affiche le mode autonome que lorsque le serveur confirme qu'il
est disponible pour cette session et ce compte.

## À la demande

**Tu choisis quand actualiser tes résultats.**

- Données conservées : aucun mot de passe IMT ; une session PASS/HUB peut
  rester disponible pour une synchronisation manuelle.
- Avantage : aucun appel planifié et exposition minimale.
- Limite : les données ne se mettent pas à jour seules.
- Après expiration : une reconnexion IMT est demandée lors de la prochaine
  synchronisation.

## Automatique avec session privée

**IMTégrale actualise tes résultats tant que la session PASS/HUB reste
valide.**

Ce mode est recommandé lorsque l'objectif est de limiter le risque tout en
bénéficiant d'une automatisation.

- Données conservées : cookies PASS/HUB filtrés et chiffrés ; aucun mot de
  passe IMT.
- Avantage : synchronisation automatique sans conserver le mot de passe.
- Limite : PASS/HUB peut fermer la session après quelques heures ou quelques
  jours.
- Après expiration : l'automatisation est mise en pause et une reconnexion est
  demandée.

## Automatique autonome

**Le worker de synchronisation peut recréer une session après son
expiration.**

- Données conservées : session PASS/HUB chiffrée et mot de passe IMT sous
  enveloppe chiffrée, après consentement explicite.
- Avantage : meilleure continuité et moins de reconnexions.
- Limites : exposition supérieure ; une compromission du worker sync ou un
  accès root au serveur pourrait exposer le mot de passe. Un changement de mot
  de passe nécessite un renouvellement.
- Après expiration : le worker essaie d'abord la session existante, puis
  utilise le credential uniquement si le mode, le consentement, la génération
  et le rollout sont toujours valides.

Le chiffrement réduit la surface d'exposition mais ne rend pas le secret
inaccessible au serveur : le worker doit pouvoir l'ouvrir pour authentifier
l'utilisateur. Python et JavaScript ne garantissent pas une zéroisation
parfaite de la mémoire.

## Consentement autonome

L'enrôlement exige une nouvelle saisie du mot de passe IMT et trois
confirmations non précochées :

1. le mot de passe sera conservé sous une enveloppe chiffrée ;
2. une compromission du worker ou du serveur pourrait l'exposer ;
3. quitter le mode supprimera irréversiblement le mot de passe conservé.

Le mot de passe de la connexion précédente n'est jamais réutilisé. Dans le
navigateur, il existe uniquement dans le champ non contrôlé et dans la variable
locale nécessaire à l'envoi HTTPS. Il n'est écrit ni dans les URL, ni dans le
stockage web, ni dans le cache TanStack Query, ni dans les journaux.

Le flux d'activation est volontairement en deux étapes :

1. vérifier le mot de passe et sceller le credential ;
2. activer le mode autonome et sa cadence.

Si la seconde étape échoue, l'interface indique **Activation à terminer**. Le
credential reste protégé mais inutilisé ; l'utilisateur peut terminer
l'activation sans ressaisir le mot de passe ou supprimer le credential.

## Changer ou supprimer

Quitter le mode autonome pour **À la demande** ou **Automatique avec session
privée** supprime immédiatement et irréversiblement l'enveloppe du mot de
passe. La session PASS/HUB peut rester utilisable jusqu'à son expiration.

L'action **Supprimer le mot de passe conservé** révoque uniquement le
credential autonome. L'action **Supprimer tout accès PASS/HUB** :

- supprime le credential ;
- révoque les sessions PASS/HUB ;
- désactive les synchronisations planifiées ;
- repasse en mode **À la demande**.

Elle ne supprime ni le compte, ni les résultats, ni les UE, ni les passkeys, ni
la session web courante.

Une reconnexion PASS/HUB reste distincte d'un renouvellement du credential :
le mot de passe saisi pour renouveler la session n'est jamais conservé ou
réutilisé automatiquement.

## État de disponibilité

G7A livre l'interface et les règles d'activation, mais la production reste
fermée :

- rollout `off` ;
- runtime autonome désactivé ;
- enrôlement désactivé ;
- aucun compte autorisé ;
- aucun credential réel.

G7B autorisera au maximum un canary après une action manuelle du propriétaire
dans son navigateur. G7C ne pourra être décidée qu'après observation des
métriques privées et revue des risques. L'indisponibilité d'un mode ne bloque
jamais l'accès au reste de l'application.
