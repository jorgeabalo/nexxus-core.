package com.aita.car;

import android.content.Intent;
import android.net.Uri;
import androidx.annotation.NonNull;
import androidx.car.app.CarAppService;
import androidx.car.app.CarContext;
import androidx.car.app.Screen;
import androidx.car.app.Session;
import androidx.car.app.validation.HostValidator;
import androidx.car.app.model.*;

/** Native POI prototype; public example locations only. No account or GPS access. */
public final class AitaCarService extends CarAppService {
 @NonNull @Override public HostValidator createHostValidator() {
  // Development head unit allowed only in debug; release uses host permission validation.
  return BuildConfig.DEBUG ? HostValidator.ALLOW_ALL_HOSTS_VALIDATOR : new HostValidator.Builder(this).build();
 }
 @NonNull @Override public Session onCreateSession() {
  return new Session() {
   @NonNull @Override public Screen onCreateScreen(@NonNull Intent intent) { return new PlacesScreen(getCarContext()); }
  };
 }
 static final class PlacesScreen extends Screen {
  PlacesScreen(CarContext context) { super(context); }
  private Row place(String title, double lat, double lon) {
   return new Row.Builder().setTitle(title).addText("Ejemplo público · no estación verificada")
    .setMetadata(new Metadata.Builder().setPlace(new Place.Builder(CarLocation.create(lat,lon)).build()).build())
    .setOnClickListener(() -> {
     try { getCarContext().startCarApp(new Intent(CarContext.ACTION_NAVIGATE,Uri.parse("geo:"+lat+","+lon))); }
     catch (RuntimeException ex) { androidx.car.app.CarToast.makeText(getCarContext(),"Navegación no disponible",androidx.car.app.CarToast.LENGTH_LONG).show(); }
    }).build();
  }
  @NonNull @Override public Template onGetTemplate() {
   ItemList list=new ItemList.Builder()
    .addItem(place("Millennium Park · Chicago",41.8826,-87.6226))
    .addItem(place("Navy Pier · Chicago",41.8917,-87.6078)).build();
   return new PlaceListMapTemplate.Builder().setTitle("AITA Car · lugares de ejemplo")
    .setHeaderAction(Action.APP_ICON).setItemList(list).build();
  }
 }
}
