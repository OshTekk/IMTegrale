# Leaderboard facultatif

## Règles de calcul

Le leaderboard contient deux classements distincts dans la même section :

- GPA sur 4, pondéré par les crédits ECTS de chaque UE ;
- moyenne générale sur 20, pondérée par les mêmes crédits ECTS.

Le calcul repart des notes brutes synchronisées depuis PASS. Les intitulés et ECTS sont importés depuis le tableau étudiant COMPETENCES avec la même session CAS ; la colonne des crédits tentés/alloués est utilisée afin qu'une UE non validée conserve son vrai coefficient. Les notes manuelles, corrections locales et masquages d'affichage ne sont jamais utilisés. Pour une UE avec rattrapage, la dernière note RAT devient la moyenne de l'UE et son grade est `E`, soit `2,5` points GPA. Toutes les UE PASS doivent disposer d'un nombre d'ECTS strictement positif avant l'inscription.

À l'activation, seule la dernière génération complète d'ECTS officiels COMPETENCES devient la base des deux scores publics. Les ECTS manuels restent disponibles pour les calculs privés, mais ne peuvent jamais entrer dans le classement. Aucune validation manuelle n'est nécessaire et la publication commence immédiatement. Une nouvelle génération COMPETENCES complète renouvelle automatiquement la base ; si elle devient incomplète pour les UE PASS, le profil est retiré sans cooldown et devra consentir de nouveau après une synchronisation valide. L'administrateur peut aussi relancer explicitement cette copie avec un motif. La migration retire tous les anciens profils actifs et révoque leur ancien consentement, tout en conservant le cooldown des utilisateurs qui s'étaient déjà retirés.

Les égalités utilisent un rang dense : deux scores identiques ont le même rang et le rang suivant est incrémenté de un. Aucun nombre minimal de participants n'est requis.

## Classification

Le prénom, le nom, le campus courant, le cursus de primo-inscription et la date prévisionnelle de sortie sont extraits individuellement de `Ma fiche` dans PASS à l'inscription, lorsqu'une valeur manque, sur demande administrateur ou après 30 jours. Rennes, Brest et Nantes sont reconnus explicitement ; un libellé de campus différent reste classé dans `autre`. Le cursus reconnaît notamment `FIP`, `FIT`, `FIL` et `FISE`, avec un libellé générique contrôlé en repli. L'année de la date de sortie devient l'année de promotion.

Ces valeurs officielles ne sont pas modifiables par l'étudiant. Une icône d'information l'oriente vers l'administrateur en cas d'erreur ; seul ce dernier peut corriger la classification académique de manière motivée et auditée. L'identité nominative est rafraîchie depuis PASS et n'est jamais remplacée par un pseudonyme.

Le classement est strictement séparé par couple `cursus + promotion`. Le campus est uniquement un filtre à l'intérieur de ce segment. Ces attributs ne figurent pas dans les lignes publiques et ne sont pas renvoyés comme champs d'un autre participant.

## Confidentialité

La section est visible par défaut, mais aucun classement, rang ou compteur n'est accessible sans participation volontaire.

À l'activation :

1. son prénom, son nom, son campus, son cursus et sa promotion officiels PASS doivent être disponibles ;
2. elle accepte explicitement la publication immédiate de son identité nominative et de ses deux scores auprès des participants déjà actifs ;
3. elle ne reçoit elle-même aucune donnée du classement pendant 48 heures ;
4. après 48 heures, elle accède aux deux classements et à leurs filtres.

Le retrait est toujours immédiat, y compris pendant les 48 premières heures. Il masque le profil à tous et déclenche uniquement un délai de 48 heures avant une éventuelle réactivation. L'effacement distinct supprime le consentement et les données propres au leaderboard sans supprimer le compte, l'identité officielle ni les notes privées. Une suppression complète du compte est disponible auprès de l'administrateur. Les sauvegardes chiffrées suivent leur cycle normal de rétention ; les données effacées ne réapparaissent pas dans la base active.

La réponse publique d'une ligne est limitée à :

```json
{
  "rank": 1,
  "official_name": "Camille Dupont",
  "score": 3.8,
  "is_self": false
}
```

## Administration

Le portail `/admin` n'est référencé dans aucune navigation étudiante. Son API exige simultanément :

- une identité `X-BotNote-Client-Identity` appartenant à `BOTNOTE_ADMIN_ALLOWED_IDENTITIES` ;
- une connexion provenant du proxy mTLS de confiance ;
- des identifiants administrateur distincts ;
- une session et un jeton CSRF dédiés, liés à l'identité réseau exacte ;
- le remplacement obligatoire du mot de passe initial.

Une requête depuis le LAN ou une autre identité reçoit `404`, y compris pour l'état de session. Les actions sensibles exigent un motif lorsque nécessaire et sont enregistrées dans `admin_audit_logs`, limité aux 10 000 entrées les plus récentes.

Le portail permet de désactiver/réactiver un compte, révoquer sessions, tokens et passkeys, supprimer un token ciblé, déclencher une synchronisation PASS motivée, relire la fiche PASS, lever un cooldown d'authentification, corriger explicitement campus/cursus/promotion, actualiser avec motif les ECTS utilisés par le classement, suspendre/restaurer/retirer une publication, lever le délai initial ou le délai de réactivation, effacer les données leaderboard et supprimer définitivement un compte. Les suppressions irréversibles exigent un motif et la confirmation littérale `SUPPRIMER`.

La section PASS de l'administration expose uniquement des agrégats sur 24 heures, 7 jours ou 30 jours : volumes, requêtes réelles, latences, réutilisation SSO, lectures de profil, refus et classes d'erreur. Les références HMAC et identifiants internes ne sont jamais renvoyés. Une sonde contrôlée exige un compte et un motif audité.

## Exploitation

Avant toute migration de production, créer et vérifier un dump PostgreSQL ainsi qu'un `vzdump` du LXC. Après migration :

1. amorcer l'administrateur avec `botnote admin-bootstrap` ;
2. lire le fichier initial uniquement via une session SSH privilégiée ;
3. se connecter par Tailscale et changer immédiatement le mot de passe ;
4. supprimer le fichier initial ;
5. vérifier le `404` admin depuis le LAN et l'accès depuis l'identité autorisée ;
6. exécuter les scénarios d'attente, retrait, réactivation et égalité dense.

Le downgrade `0003 -> 0002` détruit les tables de classement et d'administration. La révision `0004` ajoute le consentement d'actualisation automatique ; la révision `0006` ajoute les promotions officielles, passkeys et protections PASS ; la révision `0007` ajoute l'identité officielle et révoque les anciens consentements pseudonymes avant toute publication nominative ; la révision `0009` remplace la validation ECTS manuelle, révoque les consentements antérieurs au nouveau modèle de publication et retire les anciens profils actifs ; la révision `0010` ajoute la provenance et la fraîcheur des métadonnées UE officielles. En production, préférer un rollback applicatif avec la base laissée à sa révision courante, ou restaurer intégralement le dump pré-release.
