import Parent from './dummy.js';
import { Military } from '../../const.js';
import { getInt } from '../../utils.js';

class Page extends Parent {

    init() {
        this.updateUnitCount();
    }

    updateUnitCount() {
        let military = this._city.military;
        function update(type, $unit) {
            military.setCount(type, getInt($unit.text()));
        }

        let $tds = $('#tabUnits .militaryList .count td');

        update(Military.HOPLITE,           $tds.eq(1));
        update(Military.STEAM_GIANT,       $tds.eq(2));
        update(Military.SPEARMAN,          $tds.eq(3));
        update(Military.SWORDSMAN,         $tds.eq(4));
        update(Military.SLINGER,           $tds.eq(5));
        update(Military.ARCHER,            $tds.eq(6));
        update(Military.GUNNER,            $tds.eq(7));
        update(Military.BATTERING_RAM,     $tds.eq(8));
        update(Military.CATAPULT,          $tds.eq(10));
        update(Military.MORTAR,            $tds.eq(11));
        update(Military.GYROCOPTER,        $tds.eq(12));
        update(Military.BALLOON_BOMBADIER, $tds.eq(13));
        update(Military.COOK,              $tds.eq(14));
        update(Military.DOCTOR,            $tds.eq(15));
        //update(Military.SPARTAN,           $tds.eq(16));

        // === SHIPS ===
        $tds = $('#tabShips .militaryList .count td');

        update(Military.FLAME_THROWER,     $tds.eq(1));
        update(Military.STEAM_RAM,         $tds.eq(2));
        update(Military.RAM_SHIP,          $tds.eq(3));
        update(Military.CATAPULT_SHIP,     $tds.eq(4));
        update(Military.BALLISTA_SHIP,     $tds.eq(5));
        update(Military.MORTAR_SHIP,       $tds.eq(6));
        update(Military.ROCKET_SHIP,       $tds.eq(7));
        update(Military.SUBMARINE,         $tds.eq(8));
        update(Military.PADDLE_SPEED_SHIP, $tds.eq(10));
        update(Military.BALLOON_CARRIER,   $tds.eq(11));
        update(Military.TENDER,            $tds.eq(12));
    }

}

export default Page;
